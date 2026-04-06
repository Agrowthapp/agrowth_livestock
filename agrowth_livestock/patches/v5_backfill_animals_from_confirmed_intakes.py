import frappe


RECEIVED_ANIMAL_STATUSES = {
    "Normal",
    "Lastimado",
    "Problema sanitario",
    "Bajo observación",
}


def _infer_category(intake_doc, animal_row):
    if animal_row.category:
        return animal_row.category

    if animal_row.batch_line_ref and intake_doc.lines:
        for line in intake_doc.lines:
            if line.name == animal_row.batch_line_ref and line.category:
                return line.category

    if intake_doc.lines and intake_doc.lines[0].category:
        return intake_doc.lines[0].category

    return "Otro"


def _infer_weight(intake_doc, animal_row):
    if animal_row.weight:
        return animal_row.weight

    if animal_row.batch_line_ref and intake_doc.lines:
        for line in intake_doc.lines:
            if line.name == animal_row.batch_line_ref and line.avg_weight:
                return line.avg_weight

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

        for animal_row in intake.animals or []:
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
            animal.sex = animal_row.sex or "Desconocido"
            animal.current_category = _infer_category(intake, animal_row)
            animal.current_weight = _infer_weight(intake, animal_row)
            animal.company = intake.company
            animal.current_herd_batch = intake.herd_batch
            animal.warehouse = intake.warehouse
            animal.origin_type = "Purchase"
            animal.origin_document = intake.settlement
            animal.disabled = 0
            animal.insert(ignore_permissions=True)

    frappe.db.commit()
