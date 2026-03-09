from . import __version__ as app_version

app_name = "agrowth_livestock"
app_title = "Agrowth Livestock"
app_publisher = "Agrowth"
app_description = "Módulo ganadero para ERPNext - Compras, ventas, stock y trazabilidad de ganado"
app_icon = "fa fa-leanpub"
app_color = "green"
app_email = "info@agrowth.app"
app_license = "MIT"

fixtures = []

# Document Events
# ------------------

# app_include_css = "/assets/agrowth_livestock/css/agrowth_livestock.css"
# app_include_js = "/assets/agrowth_livestock/js/agrowth_livestock.js"

# website_route_rules = [
#     {"from_route": "/orders/<path:app_path>", "to_route": "orders"},
# ]

# Permissions
# -----------
# Permissions on DocTypes will be automatically applied based on configured permissions

# Migration
# ----------
migration_patches = {
    "0.0.1": ["agrowth_livestock.patches.v1_create_invoice_custom_fields.execute"]
}

# Document Events
# Hook on document methods and doctype events for processing
doc_events = {
    "Purchase Invoice": {
        "on_submit": "agrowth_livestock.utils.invoice_handlers.handle_purchase_invoice_submit",
        "on_cancel": "agrowth_livestock.utils.invoice_handlers.handle_purchase_invoice_cancel"
    },
    "Sales Invoice": {
        "on_submit": "agrowth_livestock.utils.invoice_handlers.handle_sales_invoice_submit",
        "on_cancel": "agrowth_livestock.utils.invoice_handlers.handle_sales_invoice_cancel"
    }
}

# Scheduled Tasks
# ---------------
# scheduler_events = {
#     "all": [
#         "agrowth_livestock.utils.tasks.all",
#     ],
#     "daily": [
#         "agrowth_livestock.utils.tasks.daily",
#     ],
#     "hourly": [
#         "agrowth_livestock.utils.tasks.hourly",
#     ],
#     "weekly": [
#         "agrowth_livestock.utils.tasks.weekly",
#     ],
#     "monthly": [
#         "agrowth_livestock.utils.tasks.monthly",
#     ],
# }

# Testing
# -------

# override_whitelisted_methods = {
#     "erpnext.stock.doctype.material_request.material_request.make_purchase_order": "agrowth_livestock.utils.material_request.make_purchase_order"
# }

# Overriding DocTypes
# -------------------
# Override DocType methods using the patch method

# override_doctype_dashboards = {
#     "Task": "agrowth_livestock.task.get_dashboard_data"
# }

# User Data Protection
# ----------------------

# (Do not change this variable name)
# user_data_fields = [
#     {
#         "doctype": "{doctype_1}",
#         "filters": [
#             {"field": "owner", "value": "user_id"}
#         ]
#     },
#     {
#         "doctype": "{doctype_2}",
#         "filters": [
#             {"field": "owner", "value": "user_id"}
#         ]
#     },
#     {
#         "doctype": "{doctype_3}",
#         "filters": [
#             {"field": "owner", "value": "user_id"}
#         ]
#     },
#     {
#         "doctype": "{doctype_4}",
#         "filters": [
#             {"field": "owner", "value": "user_id"}
#         ]
#     },
# ]

# Migration
# ----------
# migration_patches = {
#     "0.0.1": ["agrowth_livestock.migrations.set_first_value"]
# }

after_install = "agrowth_livestock.workspace_setup.ensure_workspaces"

after_migrate = ["agrowth_livestock.workspace_setup.ensure_workspaces"]
