import frappe


def execute():
    if not frappe.db.exists("DocField", {"parent": "Animal", "fieldname": "company"}):
        return

    animals = frappe.get_all(
        "Animal",
        fields=["name", "company", "current_herd_batch", "warehouse"],
        limit_page_length=0,
    )

    for animal in animals:
        if animal.get("company"):
            continue

        company = None
        if animal.get("current_herd_batch"):
            company = frappe.db.get_value("Herd Batch", animal["current_herd_batch"], "company")

        if not company and animal.get("warehouse"):
            company = frappe.db.get_value("Warehouse", animal["warehouse"], "company")

        if company:
            frappe.db.set_value("Animal", animal["name"], "company", company, update_modified=False)

    frappe.db.commit()
