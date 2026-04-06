import uuid

import frappe
from frappe.utils import cint


PLACEHOLDER_PREFIX = "SIN-CARAVANA-"


def generate_placeholder_ear_tag():
    return f"{PLACEHOLDER_PREFIX}{uuid.uuid4().hex[:12].upper()}"


def execute():
    intake_names = frappe.get_all(
        "Livestock Intake",
        filters={"status": ["in", ["Pendiente de ingreso", "En recepción", "Parcialmente recibido"]]},
        pluck="name",
        limit_page_length=0,
    )

    for intake_name in intake_names:
        intake = frappe.get_doc("Livestock Intake", intake_name)
        expected = cint(intake.expected_heads or 0)
        if expected <= 0:
            continue

        if intake.animals and len(intake.animals) > 0:
            continue

        for _ in range(expected):
            intake.append("animals", {
                "ear_tag_id": generate_placeholder_ear_tag(),
                "status": "Normal",
                "observation": "",
            })

        intake.save(ignore_permissions=True)

    frappe.db.commit()
