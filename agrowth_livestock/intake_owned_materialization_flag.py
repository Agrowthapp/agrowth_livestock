"""
Feature flag for the livestock-entry-settlement-boundary migration.

Owns the single source of truth for whether the new intake-owned
materialization path is active. The flag is `LIVESTOCK_ENTRY_BOUNDARY_V2`
matching the BFF convention; the value is read from the Frappe config
(`frappe.conf`) with an environment-variable fallback. Default is OFF so
the rollout is reversible (design §Migration / Rollout: 'Feature flag:
`LIVESTOCK_ENTRY_BOUNDARY_V2=off` (default off)').

This module is intentionally Frappe-coupled (it reads `frappe.conf`)
because the BFF-side flag is consulted in the same context as the
intake module. The CI tests in
`agrowth_livestock.tests.test_intake_confirm_materialization` assert
the seam at the source level (the flag is referenced in
`_submit_settlement_stock_entry` and/or `_create_and_submit_stock_entry`).
"""

import os

import frappe

FLAG_NAME = "LIVESTOCK_ENTRY_BOUNDARY_V2"


def intake_owned_materialization_enabled():
    """
    Return True when the new intake-owned materialization path
    (`_create_herd_batch_for_intake` + `_create_and_submit_stock_entry`)
    is active. Default OFF; turn ON with
    `frappe.conf[LIVESTOCK_ENTRY_BOUNDARY_V2] = True` or
    `LIVESTOCK_ENTRY_BOUNDARY_V2=1` in the environment.

    The check is centralized here so the rollout is reversible from a
    single switch and the CI regression net can pin the seam.
    """
    flag_value = None
    try:
        flag_value = frappe.conf.get(FLAG_NAME)
    except Exception:
        flag_value = None

    if flag_value is None:
        env_value = os.environ.get(FLAG_NAME)
        if env_value is not None:
            return _truthy(env_value)

    if flag_value is None:
        return False

    return _truthy(flag_value)


def _truthy(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
