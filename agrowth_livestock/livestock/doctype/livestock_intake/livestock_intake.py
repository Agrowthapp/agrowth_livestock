# Copyright (c) 2026, AgroWth and contributors
# For license information, please see license.txt

import json
import uuid

import frappe
from frappe.model.document import Document
from frappe.utils import cint

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
	def confirm_intake(self, user, mode="None"):
		"""
		Confirm the intake and activate related herd batch.
		This is the GREEN step — stock consolidation happens here.
		The draft Stock Entry created by the settlement is submitted now,
		not at settlement time, so stock only moves when hacienda physically arrives.
		"""
		if self.status == "Confirmado":
			frappe.throw("Este ingreso ya fue confirmado")

		if self.status == "Cerrado administrativamente":
			frappe.throw("No se puede confirmar un ingreso cerrado administrativamente")

		# Update intake status
		self.status = "Confirmado"
		self.confirmed_by = user
		self.confirmed_at = frappe.utils.now()
		self.confirmation_mode = mode

 		# Activate herd batch if exists
		if self.herd_batch:
			batch = frappe.get_doc("Herd Batch", self.herd_batch)
			batch.status = "Active"
			batch.confirmation_status = "Completed"
			batch.confirmation_mode = mode
			batch.confirmed_at = frappe.utils.now()
			batch.save(ignore_permissions=True)

		# Materialize received animals into Animal docs before assigning corrales.
		# Without this, stock has active Herd Batches but no real animals to drill down or move.
		self._ensure_animals_exist()

		# Assign animals to the default Acostumbramiento corral.
		# Must run before stock entry submission so warehouse is set correctly.
		self._assign_animals_to_default_corral(user, self.company)

		# Submit the draft Stock Entry created by the settlement.
		# Stock consolidation only happens at physical confirmation, not at settlement submit.
		self._submit_settlement_stock_entry(user)

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
			animal.origin_type = "Purchase"
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
		"""
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
			f"confirmed by {user}"
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
		"""
		if not self.settlement:
			return

		settlement = frappe.get_doc("Livestock Settlement", self.settlement)
		if not settlement.stock_entry:
			return

		se = frappe.get_doc("Stock Entry", settlement.stock_entry)
		if se.docstatus == 2:
			# Already cancelled — idempotent
			return

		if se.docstatus == 1:
			se.cancel()
			frappe.logger().info(
				f"[livestock_intake] Stock Entry {se.name} cancelled on intake {self.name} "
				f"reverted by {user}"
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
