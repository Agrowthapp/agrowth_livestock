# Copyright (c) 2026, AgroWth and contributors
# For license information, please see license.txt

import json
import uuid

import frappe
from frappe.model.document import Document
from frappe.utils import cint

from agrowth_livestock.intake_owned_materialization_flag import (
    intake_owned_materialization_enabled,
)

PLACEHOLDER_PREFIX = "SIN-CARAVANA-"


def generate_placeholder_ear_tag():
	"""Generate a unique placeholder ear tag ID for animals without a real EID."""
	return f"{PLACEHOLDER_PREFIX}{uuid.uuid4().hex[:12].upper()}"


VALID_ANIMAL_STATUSES = {
	"Normal",
	"Lastimado",
	"Problema sanitario",
	"Muerto al arribo",
	"No llegó",
	"Rechazado",
	"Bajo observación",
}

RECEIVED_ANIMAL_STATUSES = {
	"Normal",
	"Lastimado",
	"Problema sanitario",
	"Bajo observación",
}


class LivestockIntake(Document):
	"""
	Livestock Intake represents the operational receipt layer between
	commercial settlement expectation and actual physical arrival.
	"""
	
	def validate(self):
		"""Validate intake before save"""
		self.ensure_animals_seeded()
		self.validate_animal_statuses()
		self.sync_received_heads_from_animals()
		self.calculate_discrepancies()
		self.update_discrepancy_flag()

	def ensure_animals_seeded(self):
		"""
		If the intake has expected heads but no animal rows yet, seed placeholder rows so
		operators can complete them manually or overwrite them via EID upload.
		"""
		expected = cint(self.expected_heads or 0)
		if expected <= 0:
			return

		if self.animals and len(self.animals) > 0:
			return

		for _ in range(expected):
			self.append("animals", {
				"ear_tag_id": generate_placeholder_ear_tag(),
				"status": "Normal",
				"observation": "",
			})

	def validate_animal_statuses(self):
		for animal in self.animals or []:
			status = animal.status or "Normal"
			if status not in VALID_ANIMAL_STATUSES:
				frappe.throw(f"Estado de animal inválido: {status}")

	def sync_received_heads_from_animals(self):
		animals = self.animals or []
		self.received_heads = sum(1 for animal in animals if (animal.status or "Normal") in RECEIVED_ANIMAL_STATUSES)
		self.problem_heads = sum(
			1 for animal in animals if (animal.status or "Normal") in {"Lastimado", "Problema sanitario", "Bajo observación"}
		)
	
	def calculate_discrepancies(self):
		"""Calculate missing/surplus heads based on expected vs received"""
		expected = self.expected_heads or 0
		received = self.received_heads or 0
		
		if received < expected:
			self.missing_heads = expected - received
			self.surplus_heads = 0
		elif received > expected:
			self.surplus_heads = received - expected
			self.missing_heads = 0
		else:
			self.missing_heads = 0
			self.surplus_heads = 0
	
	def update_discrepancy_flag(self):
		"""Mark intake as having discrepancy if expected != received"""
		self.has_discrepancy = (self.expected_heads != self.received_heads)
	
	@frappe.whitelist()
	def confirm_intake(self, user, mode="None", herd_batch=None):
		"""
		Confirm the intake and activate related herd batch.
		This is the GREEN step — stock consolidation happens here.

		PR2 livestock-entry-settlement-boundary: physical materialization
		(Herd Batch + Stock Entry) is owned by the intake, not the
		settlement. The intake creates the Herd Batch and submits the
		Stock Entry on confirm. The legacy `_submit_settlement_stock_entry`
		path remains available behind `LIVESTOCK_ENTRY_BOUNDARY_V2` for
		migration only.
		"""
		if self.status == "Confirmado":
			frappe.throw("Este ingreso ya fue confirmado")

		if self.status == "Cerrado administrativamente":
			frappe.throw("No se puede confirmar un ingreso cerrado administrativamente")

		# BUG 1 fix (defense in depth): v9-migrated intakes (or any
		# intake created from a settlement that has no `warehouse`) reach
		# the confirm flow with an empty `self.warehouse`. The field is
		# `reqd: 1` on the doctype, so `self.save()` at the end of this
		# method would raise `MandatoryError: warehouse` from Frappe
		# model validation. The API layer (`api/intakes.py:confirm_intake`)
		# also resolves a fallback, but direct Frappe form submissions or
		# custom scripts bypass the API — this guard catches them.
		# Resolution order: settlement warehouse, then default
		# Acostumbramiento corral for the company.
		if not self.warehouse:
			resolved_warehouse = None
			if self.settlement:
				resolved_warehouse = frappe.db.get_value(
					"Livestock Settlement", self.settlement, "warehouse"
				)
			if not resolved_warehouse:
				resolved_warehouse = self._resolve_default_acostumbramiento_corral(self.company)
			if resolved_warehouse:
				self.warehouse = resolved_warehouse
				self.db_set("warehouse", resolved_warehouse, update_modified=False)

		# Update intake status
		self.status = "Confirmado"
		self.confirmed_by = user
		self.confirmed_at = frappe.utils.now()
		self.confirmation_mode = mode

		# PR2 boundary: intake owns the Herd Batch creation. If the
		# intake was migrated from a settlement-first flow, the legacy
		# `self.herd_batch` pointer may already be set — in that case
		# `_create_herd_batch_for_intake` activates it instead of
		# creating a new one.
		self._create_herd_batch_for_intake(user, mode)

		# Materialize received animals into Animal docs before assigning corrales.
		# Without this, stock has active Herd Batches but no real animals to drill down or move.
		self._ensure_animals_exist()

		# Assign animals to the default Acostumbramiento corral.
		# Must run before stock entry submission so warehouse is set correctly.
		self._assign_animals_to_default_corral(user, self.company)

		# PR2 boundary: intake owns the Stock Entry creation + submission.
		# The legacy path (`_submit_settlement_stock_entry`) is kept for
		# migration only behind the `LIVESTOCK_ENTRY_BOUNDARY_V2` flag.
		self._create_and_submit_stock_entry(user)

		self.save(ignore_permissions=True)

		# Log action
		self.log_action("confirmed", user, {"mode": mode})

		return self

	def _resolve_default_acostumbramiento_corral(self, company):
		"""
		Returns the name of the first active Acostumbramiento corral warehouse
		for the given company, or None if none exists.
		"""
		results = frappe.get_all(
			"Warehouse",
			filters={
				"company": company,
				"disabled": 0,
				"is_group": 0,
				"is_corral": 1,
				"corral_type": "Acostumbramiento",
			},
			fields=["name"],
			limit=1,
		)
		return results[0]["name"] if results else None

	def _infer_category_for_animal(self, animal_row):
		if animal_row.get("category"):
			return animal_row.get("category")

		batch_line_ref = animal_row.get("batch_line_ref")
		if batch_line_ref and self.lines:
			for line in self.lines:
				if line.name == batch_line_ref and line.category:
					return line.category

		if self.lines and self.lines[0].category:
			return self.lines[0].category

		return "Otro"

	def _infer_weight_for_animal(self, animal_row):
		weight = animal_row.get("weight")
		if weight:
			return weight

		batch_line_ref = animal_row.get("batch_line_ref")
		if batch_line_ref and self.lines:
			for line in self.lines:
				line_weight = getattr(line, "avg_weight", None)
				if line.name == batch_line_ref and line_weight:
					return line_weight

		if self.lines:
			first_weight = getattr(self.lines[0], "avg_weight", None)
			if first_weight:
				return first_weight

		return None

	def _ensure_animals_exist(self):
		"""
		Create missing Animal docs for all received animals staged on the intake.
		This is the canonical point where physical animals become stock-traceable entities.
		"""
		for animal_row in self.animals or []:
			status = animal_row.status or "Normal"
			if status not in RECEIVED_ANIMAL_STATUSES:
				continue

			ear_tag_id = (animal_row.ear_tag_id or "").strip()
			if not ear_tag_id:
				continue

			if frappe.db.exists("Animal", {"ear_tag_id": ear_tag_id}):
				continue

			animal = frappe.new_doc("Animal")
			animal.ear_tag_id = ear_tag_id
			animal.species = "Bovino"
			animal.sex = animal_row.get("sex") or "Desconocido"
			animal.current_category = self._infer_category_for_animal(animal_row)
			animal.current_weight = self._infer_weight_for_animal(animal_row)
			animal.company = self.company
			animal.current_herd_batch = self.herd_batch
			animal.warehouse = self.warehouse
			if self.settlement:
				animal.origin_type = "Livestock Settlement"
				animal.origin_document = self.settlement
			animal.disabled = 0
			animal.insert(ignore_permissions=True)

	def _assign_animals_to_default_corral(self, user, company):
		"""
		After intake confirmation, assign all staged animals to the default
		Acostumbramiento corral. Graceful fallback: if no corral exists,
		uses the intake's own warehouse. Never blocks confirmation.
		"""
		target_warehouse = self._resolve_default_acostumbramiento_corral(company)

		if not target_warehouse:
			# Fallback to intake warehouse — do not block confirmation
			target_warehouse = self.warehouse
			if not target_warehouse:
				return

		for animal_row in self.animals or []:
			try:
				animal_doc = frappe.get_doc("Animal", {"ear_tag_id": animal_row.ear_tag_id})
				if animal_doc.warehouse != target_warehouse:
					animal_doc.warehouse = target_warehouse
					animal_doc.save(ignore_permissions=True)

					# Audit event
					frappe.get_doc({
						"doctype": "Animal Event",
						"animal": animal_doc.name,
						"event_type": "Movimiento",
						"event_date": frappe.utils.now(),
						"new_warehouse": target_warehouse,
						"notes": f"Auto-asignado al corral de acostumbramiento en confirmación de ingreso {self.name}",
					}).insert(ignore_permissions=True)
			except frappe.DoesNotExistError:
				# Animal not yet persisted or already cleaned up — skip
				continue
			except Exception as e:
				frappe.logger().warning(
					f"[livestock_intake] Could not assign animal {animal_row.ear_tag_id} "
					f"to corral {target_warehouse}: {e}"
				)

	def _submit_settlement_stock_entry(self, user):
		"""
		Submit the draft Stock Entry linked to the originating settlement.
		Called only during confirm_intake — this is the canonical stock posting point.
		No-op if the settlement has no stock entry or it is already submitted.

		PR2 livestock-entry-settlement-boundary: this helper is the
		LEGACY path. It is kept for migration only and is gated by the
		`LIVESTOCK_ENTRY_BOUNDARY_V2` feature flag. The canonical
		post-PR2 path is `_create_and_submit_stock_entry`, which the
		intake owns end-to-end.

		When the flag is OFF (default), this helper is the no-op
		fallback that submits the settlement-created draft stock entry
		so legacy sites do not lose the path on upgrade. When the flag
		is ON, the new path is used and this helper is bypassed.
		"""
		if intake_owned_materialization_enabled():
			# PR2 path: the new `_create_and_submit_stock_entry` is the
			# canonical stock posting point. The legacy path is bypassed
			# when the rollout flag is on.
			return

		if not self.settlement:
			return

		settlement = frappe.get_doc("Livestock Settlement", self.settlement)
		if not settlement.stock_entry:
			return

		se = frappe.get_doc("Stock Entry", settlement.stock_entry)
		if se.docstatus == 1:
			# Already submitted — idempotent, nothing to do
			return

		if se.docstatus != 0:
			frappe.throw(
				f"La Entrada de Stock {se.name} está en estado inválido (docstatus={se.docstatus}) "
				"y no puede consolidarse. Revisá el documento manualmente."
			)

		se.submit()
		frappe.logger().info(
			f"[livestock_intake] Stock Entry {se.name} submitted on intake {self.name} "
			f"confirmed by {user} (legacy path)"
		)

	def _create_herd_batch_for_intake(self, user, mode):
		"""
		PR2 livestock-entry-settlement-boundary: create the Herd Batch
		from the intake, not the settlement.

		Two cases:
		  1. The intake was created post-PR2 by `Livestock Settlement.create_livestock_intake`,
		     which does NOT create a Herd Batch (settlement is strictly
		     administrative). This method creates a fresh Herd Batch
		     and persists the pointer on `self.herd_batch`.
		  2. The intake was migrated from a settlement-first legacy
		     flow where the settlement already created a Herd Batch.
		     In that case `self.herd_batch` is already set; this method
		     activates it and updates its confirmation fields.

		The Herd Batch's `origin_type` is set to "Livestock Intake" so
		the operational track is the source of truth for the artifact
		(per design §Architecture Decisions).
		"""
		# Case 2: legacy intake — Herd Batch already exists, just activate it.
		if self.herd_batch and frappe.db.exists("Herd Batch", self.herd_batch):
			batch = frappe.get_doc("Herd Batch", self.herd_batch)
			batch.status = "Active"
			if hasattr(batch, "confirmation_status"):
				batch.confirmation_status = "Completed"
			if hasattr(batch, "confirmation_mode"):
				batch.confirmation_mode = mode
			if hasattr(batch, "confirmed_at"):
				batch.confirmed_at = frappe.utils.now()
			batch.save(ignore_permissions=True)
			frappe.logger().info(
				f"[livestock_intake] Herd Batch {batch.name} activated on intake "
				f"{self.name} confirmed by {user} (legacy artifact)"
			)
			return

		# Case 1: post-PR2 intake — no Herd Batch exists, create one.
		if not self.warehouse:
			frappe.throw(
				"No se puede crear la tropa: el ingreso no tiene depósito definido"
			)

		batch = frappe.new_doc("Herd Batch")
		batch.company = self.company
		batch.warehouse = self.warehouse
		batch.arrival_date = self.posting_date
		batch.status = "Active"
		# PR2 boundary: origin is the intake (operational track), not
		# the settlement. The settlement reference is kept on the
		# intake row and reachable via the reverse link (F.1).
		batch.origin_type = "Livestock Intake"
		if hasattr(batch, "origin_document"):
			batch.origin_document = self.name
		if hasattr(batch, "confirmation_status"):
			batch.confirmation_status = "Completed"
		if hasattr(batch, "confirmation_mode"):
			batch.confirmation_mode = mode
		if hasattr(batch, "confirmed_at"):
			batch.confirmed_at = frappe.utils.now()
		batch.notes = f"Generado por confirmación de ingreso {self.name}"

		# Build Herd Batch lines from intake lines so per-category
		# breakdown is preserved at the batch level.
		for line in self.lines or []:
			batch_line = batch.append("lines")
			if hasattr(batch_line, "species"):
				batch_line.species = line.species or "Bovino"
			if hasattr(batch_line, "category"):
				batch_line.category = line.category or "Otro"
			if hasattr(batch_line, "item_code"):
				batch_line.item_code = line.item_code
			if hasattr(batch_line, "qty_heads"):
				batch_line.qty_heads = line.expected_heads or 0
			if hasattr(batch_line, "avg_weight"):
				batch_line.avg_weight = getattr(line, "avg_weight", None)
			if hasattr(batch_line, "total_weight"):
				batch_line.total_weight = getattr(line, "total_weight", None)
			if hasattr(batch_line, "unit_price"):
				batch_line.unit_price = getattr(line, "unit_price", None)
			if hasattr(batch_line, "amount"):
				batch_line.amount = getattr(line, "amount", None)

		batch.insert(ignore_permissions=True)

		# PR2 boundary: persist the canonical reverse pointer so the
		# intake row carries the join key to the Herd Batch. The BFF
		# reads this pointer to surface `herdBatch` on the intake DTO.
		self.db_set("herd_batch", batch.name, update_modified=False)

		frappe.logger().info(
			f"[livestock_intake] Herd Batch {batch.name} created on intake "
			f"{self.name} confirmed by {user}"
		)

	def _create_and_submit_stock_entry(self, user):
		"""
		PR2 livestock-entry-settlement-boundary: create + submit the
		Stock Entry from the intake, not the settlement. This is the
		canonical post-PR2 stock posting point.

		Behavior:
		  - When the intake is post-PR2, no Stock Entry exists yet;
		    this method creates a Material Receipt Stock Entry and
		    submits it. The Stock Entry name is persisted on the
		    settlement's `stock_entry` field for backwards-compat
		    reads (the BFF now reads from the intake, not the
		    settlement, but legacy code paths may still look at the
		    settlement).
		  - When the intake is migrated from a settlement-first legacy
		    flow and the legacy helper ran first (flag OFF), the
		    Stock Entry already exists and this method is a no-op.
		  - When the flag is OFF, the legacy helper is the actual
		    posting point and this method falls back to it via the
		    `LIVESTOCK_ENTRY_BOUNDARY_V2` flag check below.
		"""
		if not intake_owned_materialization_enabled():
			# Legacy rollout: defer to the settlement-owned draft
			# Stock Entry path. The legacy helper is the canonical
			# posting point when the flag is off; this method is the
			# bypass.
			return self._submit_settlement_stock_entry(user)

		if not self.warehouse:
			frappe.throw(
				"No se puede crear la entrada de stock: el ingreso no tiene depósito definido"
			)

		# If the legacy helper already submitted a settlement-owned
		# stock entry, leave it alone. The intake pointer is consistent
		# with the settlement's stock_entry field in that case.
		if self.settlement:
			settlement = frappe.get_doc("Livestock Settlement", self.settlement)
			if settlement.stock_entry:
				se = frappe.get_doc("Stock Entry", settlement.stock_entry)
				if se.docstatus == 1:
					# Mirror the legacy pointer on the intake row so
					# the BFF intake DTO can read it. The field is
					# declared on the doctype, so `self.stock_entry`
					# is always defined post-PR2.
					if getattr(self, "stock_entry", None) != se.name:
						self.db_set("stock_entry", se.name, update_modified=False)
					return

		# Canonical path: create a fresh Material Receipt Stock Entry
		# owned by the intake. The settlement has no `stock_entry`
		# pointer in this branch (the PR2 boundary is in effect).
		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = "Material Receipt"
		se.purpose = "Material Receipt"
		se.company = self.company
		se.posting_date = self.posting_date

		for line in self.lines or []:
			se_item = se.append("items")
			se_item.item_code = line.item_code
			se_item.qty = line.expected_heads or 0
			se_item.t_warehouse = self.warehouse
			se_item.conversion_factor = 1

		se.insert(ignore_permissions=True)
		se.submit()

		# Persist the pointer on the intake row so the BFF can surface
		# `stockEntry` on the intake DTO. The field is declared on the
		# doctype, so the assignment is unconditional. The
		# `_cancel_settlement_stock_entry` revert helper reads this
		# field to cancel the intake-owned Stock Entry on revert.
		self.db_set("stock_entry", se.name, update_modified=False)

		frappe.logger().info(
			f"[livestock_intake] Stock Entry {se.name} created and submitted "
			f"on intake {self.name} confirmed by {user} (PR2 path)"
		)

	@frappe.whitelist()
	def revert_intake(self, user, reason):
		"""
		Revert a confirmed intake back to pending state.
		Cancels the submitted Stock Entry so stock is reversed immediately.
		"""
		if self.status != "Confirmado":
			frappe.throw("Solo se puede revertir un ingreso confirmado")

		if self.status == "Cerrado administrativamente":
			frappe.throw("No se puede revertir un ingreso cerrado administrativamente")

		# Cancel the submitted Stock Entry before changing status
		self._cancel_settlement_stock_entry(user)

		# Update intake status
		self.status = "Revertido"
		self.reverted_by = user
		self.reverted_at = frappe.utils.now()
		self.revert_reason = reason

		# Revert herd batch to pending
		if self.herd_batch:
			batch = frappe.get_doc("Herd Batch", self.herd_batch)
			batch.status = "Pending Entry"
			batch.confirmation_status = "Pending"
			batch.save(ignore_permissions=True)

		self.save(ignore_permissions=True)

		# Log action
		self.log_action("reverted", user, {"reason": reason})

		return self

	def _cancel_settlement_stock_entry(self, user):
		"""
		Cancel a submitted Stock Entry when reverting an intake.
		No-op if already cancelled or not yet submitted (draft).

		PR2 livestock-entry-settlement-boundary: the intake OWNS the
		Stock Entry post-PR2 (`self.stock_entry`). Legacy intakes still
		have a settlement-owned Stock Entry (one created by the
		pre-PR2 settlement path). The helper covers both seams:

		  1. Settlement-owned branch — legacy intakes. The settlement's
		     `stock_entry` field carries the Stock Entry name.
		  2. Intake-owned branch — post-PR2 intakes. The intake's own
		     `stock_entry` field (set by `_create_and_submit_stock_entry`)
		     carries the Stock Entry name.

		The helper first tries the settlement-owned branch (legacy).
		If absent, it falls back to the intake-owned branch. This
		keeps legacy sites working and unblocks the post-PR2 path
		that the PR 2 review found.
		"""
		# Branch 1: settlement-owned (legacy intakes)
		if self.settlement:
			settlement = frappe.get_doc("Livestock Settlement", self.settlement)
			if settlement.stock_entry:
				se = frappe.get_doc("Stock Entry", settlement.stock_entry)
				if se.docstatus == 2:
					# Already cancelled — idempotent
					return

				if se.docstatus == 1:
					se.cancel()
					frappe.logger().info(
						f"[livestock_intake] Stock Entry {se.name} cancelled on intake {self.name} "
						f"reverted by {user} (settlement-owned legacy path)"
					)
					return

		# Branch 2: intake-owned (post-PR2). The settlement path did
		# not produce a Stock Entry, so the only artifact to cancel
		# is the one the intake created on confirm.
		if not getattr(self, "stock_entry", None):
			return

		se = frappe.get_doc("Stock Entry", self.stock_entry)
		if se.docstatus == 2:
			# Already cancelled — idempotent
			return

		if se.docstatus == 1:
			se.cancel()
			frappe.logger().info(
				f"[livestock_intake] Stock Entry {se.name} cancelled on intake {self.name} "
				f"reverted by {user} (intake-owned PR2 path)"
			)
	
	def log_action(self, action, user, payload=None):
		"""
		Log operational actions to audit trail.
		For v1 we store in a simple text log, future: separate doctype.
		"""
		log_entry = {
			"action": action,
			"user": user,
			"timestamp": frappe.utils.now(),
			"payload": payload or {}
		}
		
		# For now, append to notes field as JSON
		# In future slice: move to proper Livestock Intake Log child table
		current_notes = self.notes or ""
		log_line = f"\n[{log_entry['timestamp']}] {action} by {user}"
		if payload:
			log_line += f" - {json.dumps(payload)}"
		
		self.notes = current_notes + log_line

	def stage_animals(self, user, animals, source="manual"):
		if self.status == "Confirmado":
			frappe.throw("No se pueden modificar animales de un ingreso confirmado")

		self.set("animals", [])
		for animal in animals:
			status = animal.get("status") or "Normal"
			if status not in VALID_ANIMAL_STATUSES:
				frappe.throw(f"Estado de animal inválido: {status}")

			# Generate placeholder EID for animals without individualization
			ear_tag_id = animal.get("ear_tag_id") or ""
			if not ear_tag_id.strip():
				ear_tag_id = generate_placeholder_ear_tag()

			self.append("animals", {
				"ear_tag_id": ear_tag_id,
				"status": status,
				"observation": animal.get("observation") or "",
				"weight": animal.get("weight"),
				"batch_line_ref": animal.get("batch_line_ref"),
				"is_duplicate_in_upload": 1 if animal.get("is_duplicate_in_upload") else 0,
				"matches_existing_animal": animal.get("matches_existing_animal") or "",
			})

		self.save(ignore_permissions=True)
		self.log_action(
			f"animals_loaded_{source}",
			user,
			{"count": len(animals), "source": source},
		)
		self.save(ignore_permissions=True)
		return self
