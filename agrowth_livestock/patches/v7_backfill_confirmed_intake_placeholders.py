import uuid

import frappe
from frappe.utils import cint


PLACEHOLDER_PREFIX = "SIN-CARAVANA-"


def generate_placeholder_ear_tag():
    return f"{PLACEHOLDER_PREFIX}{uuid.uuid4().hex[:12].upper()}"


def _infer_category(intake_doc):
    if intake_doc.lines and intake_doc.lines[0].category:
        return intake_doc.lines[0].category
    return "Otro"


def _infer_weight(intake_doc):
    if intake_doc.lines and intake_doc.lines[0].avg_weight:
        return intake_doc.lines[0].avg_weight
    return None


def execute():
    intake_names = frappe.get_all(
        "Livestock Intake",
        filters={"status": "Confirmado"},
        pluck="name",
        limit_page_length=0,
    )

    for intake_name in intake_names:
        intake = frappe.get_doc("Livestock Intake", intake_name)
        expected = cint(intake.expected_heads or 0)
        if expected <= 0:
            continue

        if not intake.animals or len(intake.animals) == 0:
            for _ in range(expected):
                intake.append("animals", {
                    "ear_tag_id": generate_placeholder_ear_tag(),
                    "status": "Normal",
                    "observation": "Backfill automático de intake confirmado sin grilla de animales",
                })
            intake.save(ignore_permissions=True)

        for animal_row in intake.animals or []:
            ear_tag_id = (animal_row.ear_tag_id or "").strip()
            if not ear_tag_id:
                continue

            if frappe.db.exists("Animal", {"ear_tag_id": ear_tag_id}):
                continue

            animal = frappe.new_doc("Animal")
            animal.ear_tag_id = ear_tag_id
            animal.species = "Bovino"
            animal.sex = animal_row.sex or "Desconocido"
            animal.current_category = animal_row.category or _infer_category(intake)
            animal.current_weight = animal_row.weight or _infer_weight(intake)
            animal.company = intake.company
            animal.current_herd_batch = intake.herd_batch
            animal.warehouse = intake.warehouse
            animal.origin_type = "Purchase"
            animal.origin_document = intake.settlement
            animal.disabled = 0
            animal.insert(ignore_permissions=True)

    frappe.db.commit()
