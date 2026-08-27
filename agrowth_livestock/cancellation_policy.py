"""
Livestock Settlement cancellation policy.

Pure (Frappe-free) helper that decides whether a settlement may be cancelled
given the current status of the linked Livestock Intake. Imported by both Frappe
doctype trees so the seam is symmetric and drift-resistant.

The policy is the single source of truth for the cancel guard. Any change to the
list of blocking or allowed statuses MUST be made here, not in the doctype
controllers, so the rules can be tested in isolation and stay in sync across
the duplicate Frappe tree.

Status set source: `Livestock Intake` doctype options (status field).
The unknown-status default is intentionally blocking to protect stock:
a brand-new status we have not whitelisted must not silently allow cancel
and orphan an active Herd Batch / Stock Entry.
"""

# Intake statuses for which the linked settlement MUST NOT be cancelled.
# If the intake has physical stock on the ground, cancelling the settlement
# would leave an Active Herd Batch + submitted Stock Entry + confirmed Intake
# without its Purchase Invoice.
ACTIVE_INTAKE_STATUSES = frozenset({
    "Confirmado",
    "Parcialmente recibido",
    "En recepción",
    "Con discrepancia",
    "Cerrado administrativamente",
})

# Intake statuses for which the linked settlement MAY be cancelled.
# The intake is either pre-physical (Pendiente de ingreso) or already
# administratively unwound (Revertido).
ALLOWED_INTAKE_STATUSES_FOR_CANCEL = frozenset({
    "Pendiente de ingreso",
    "Revertido",
})


def resolve_intake_status_blocking_cancel(intake_status, intake_name):
    """
    Decide whether a settlement cancel is blocked by the linked intake.

    Returns:
        None  — cancel is allowed.
        dict  — cancel is blocked. Dict shape:
                  {"message": str, "title": str}
                The message references the intake name and its current status
                so the operator knows exactly what to revert or close before
                retrying the cancel.

    The helper is pure: same input → same output, no side effects, no Frappe
    coupling. It is safe to call from any context, including bench migrations
    and unit tests.
    """
    if intake_status in ALLOWED_INTAKE_STATUSES_FOR_CANCEL:
        return None

    if not intake_status:
        # Defensive: a None or empty status means we have no evidence the
        # intake is in a safe state. Block by default.
        return {
            "title": "Cancelación bloqueada por ingreso activo",
            "message": (
                "No se puede cancelar la liquidación: el ingreso {0} no tiene "
                "estado definido. Verificá el ingreso antes de cancelar la "
                "liquidación."
            ).format(intake_name),
        }

    if intake_status in ACTIVE_INTAKE_STATUSES:
        return {
            "title": "Cancelación bloqueada por ingreso activo",
            "message": (
                "No se puede cancelar la liquidación: el ingreso {0} está en "
                "estado {1}. Revertí o cerrá el ingreso antes de cancelar la "
                "liquidación."
            ).format(intake_name, intake_status),
        }

    # Unknown status — fail closed. This is the defensive default that keeps
    # the policy safe when a new status is added to the Doctype before this
    # helper is updated.
    return {
        "title": "Cancelación bloqueada por ingreso activo",
        "message": (
            "No se puede cancelar la liquidación: el ingreso {0} está en "
            "estado {1}, que requiere revisión manual. Revertí o cerrá el "
            "ingreso antes de cancelar la liquidación."
        ).format(intake_name, intake_status),
    }
