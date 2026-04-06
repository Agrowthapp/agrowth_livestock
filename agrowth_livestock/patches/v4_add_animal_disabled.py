import frappe


def execute():
    if not frappe.db.exists("DocField", {"parent": "Animal", "fieldname": "disabled"}):
        return

    animals = frappe.get_all("Animal", fields=["name"], limit_page_length=0)
    for animal in animals:
        frappe.db.set_value("Animal", animal["name"], "disabled", 0, update_modified=False)

    frappe.db.commit()
