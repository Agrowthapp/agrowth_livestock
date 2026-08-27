"""
Test for the Settlement cancel-guard helper (livestock-entry-settlement-boundary PR1).

Covers task 1.2 of the design: settlement cancel MUST be blocked when the linked
Livestock Intake is in an active state; allowed when Pendiente de ingreso or
Revertido.

The production code lives in a small, Frappe-free helper module so the
cancellation policy can be unit-tested without a bench:

  `agrowth_livestock.cancellation_policy.resolve_intake_status_blocking_cancel`

Both Frappe doctype trees (`agrowth_livestock/doctype/...` and
`agrowth_livestock/livestock/doctype/...`) wire the helper into their
`on_cancel` hooks so the seam is symmetric and drift-resistant.

The helper module lives at the package root (not under `utils/`) on purpose:
`agrowth_livestock/utils/__init__.py` imports `frappe` at module-load time, and
the policy must be testable in CI without a bench.
"""

import unittest

from agrowth_livestock.cancellation_policy import (
    ACTIVE_INTAKE_STATUSES,
    ALLOWED_INTAKE_STATUSES_FOR_CANCEL,
    resolve_intake_status_blocking_cancel,
)


class CancelGuardHelperTests(unittest.TestCase):
    def test_blocks_cancel_when_intake_is_confirmado(self):
        result = resolve_intake_status_blocking_cancel("Confirmado", "LI-2026-0001")
        self.assertIsNotNone(result, "Cancel must be blocked when intake is Confirmado")
        self.assertIn("LI-2026-0001", result["message"])
        self.assertEqual(result["title"], "Cancelación bloqueada por ingreso activo")

    def test_blocks_cancel_when_intake_is_parcialmente_recibido(self):
        result = resolve_intake_status_blocking_cancel(
            "Parcialmente recibido", "LI-2026-0002"
        )
        self.assertIsNotNone(
            result, "Cancel must be blocked when intake is Parcialmente recibido"
        )

    def test_blocks_cancel_when_intake_is_en_recepcion(self):
        result = resolve_intake_status_blocking_cancel("En recepción", "LI-2026-0003")
        self.assertIsNotNone(result, "Cancel must be blocked when intake is En recepción")

    def test_blocks_cancel_when_intake_is_con_discrepancia(self):
        result = resolve_intake_status_blocking_cancel("Con discrepancia", "LI-2026-0004")
        self.assertIsNotNone(result, "Cancel must be blocked when intake is Con discrepancia")

    def test_blocks_cancel_when_intake_is_cerrado_administrativamente(self):
        result = resolve_intake_status_blocking_cancel(
            "Cerrado administrativamente", "LI-2026-0005"
        )
        self.assertIsNotNone(
            result, "Cancel must be blocked when intake is Cerrado administrativamente"
        )

    def test_allows_cancel_when_intake_is_pendiente(self):
        result = resolve_intake_status_blocking_cancel(
            "Pendiente de ingreso", "LI-2026-0006"
        )
        self.assertIsNone(result, "Cancel must be allowed when intake is Pendiente de ingreso")

    def test_allows_cancel_when_intake_is_revertido(self):
        result = resolve_intake_status_blocking_cancel("Revertido", "LI-2026-0007")
        self.assertIsNone(result, "Cancel must be allowed when intake is Revertido")

    def test_helper_is_pure(self):
        first = resolve_intake_status_blocking_cancel("Confirmado", "LI-X")
        second = resolve_intake_status_blocking_cancel("Confirmado", "LI-X")
        self.assertEqual(first, second)

    def test_error_includes_intake_name_and_status(self):
        result = resolve_intake_status_blocking_cancel("Confirmado", "LI-ERR-001")
        self.assertIsNotNone(result)
        self.assertIn("LI-ERR-001", result["message"])
        self.assertIn("Confirmado", result["message"])

    def test_active_set_is_disjoint_from_allowed_set(self):
        self.assertEqual(
            ACTIVE_INTAKE_STATUSES & ALLOWED_INTAKE_STATUSES_FOR_CANCEL,
            set(),
            "An intake status cannot be both active (block cancel) and allowed-to-cancel",
        )

    def test_active_set_covers_all_blocking_statuses(self):
        # If a new blocking status is added in the spec, it MUST appear in
        # ACTIVE_INTAKE_STATUSES. Keep the regression net tight.
        for status in (
            "Confirmado",
            "Parcialmente recibido",
            "En recepción",
            "Con discrepancia",
            "Cerrado administrativamente",
        ):
            self.assertIn(
                status,
                ACTIVE_INTAKE_STATUSES,
                f"Status {status!r} must be classified as active/blocking",
            )

    def test_allowed_set_covers_pendiente_and_revertido(self):
        for status in ("Pendiente de ingreso", "Revertido"):
            self.assertIn(
                status,
                ALLOWED_INTAKE_STATUSES_FOR_CANCEL,
                f"Status {status!r} must be classified as allowed for cancel",
            )

    def test_unknown_status_defaults_to_blocking(self):
        # Defensive default: a status we have not whitelisted must NOT allow
        # settlement cancel. Better to over-block than to orphan active stock.
        result = resolve_intake_status_blocking_cancel("Estado futuro", "LI-FUT-001")
        self.assertIsNotNone(
            result,
            "Unknown intake status must default to blocking to protect stock",
        )

    def test_triangulation_status_grid_distinguishes_blocking_from_allowed(self):
        # Triangulation: the policy is not a hardcoded if/else for one status.
        # It MUST distinguish every blocking status from every allowed status
        # using set membership, not equality.
        blocking = ("Confirmado", "Parcialmente recibido", "En recepción",
                    "Con discrepancia", "Cerrado administrativamente")
        allowed = ("Pendiente de ingreso", "Revertido")
        for status in blocking:
            self.assertIsNotNone(
                resolve_intake_status_blocking_cancel(status, "LI-TRI"),
                f"Status {status!r} must block cancel",
            )
        for status in allowed:
            self.assertIsNone(
                resolve_intake_status_blocking_cancel(status, "LI-TRI"),
                f"Status {status!r} must allow cancel",
            )

    def test_triangulation_intake_name_propagation(self):
        # The intake name MUST appear in the error message verbatim across
        # several blocking statuses, so a generic helper that ignored the
        # name parameter would fail this test.
        names = ["LI-001", "LI-002-very-long-name", "LI-with-dashes-123"]
        for name in names:
            for status in ("Confirmado", "Con discrepancia"):
                result = resolve_intake_status_blocking_cancel(status, name)
                self.assertIsNotNone(result)
                self.assertIn(name, result["message"])


if __name__ == "__main__":
    unittest.main()
