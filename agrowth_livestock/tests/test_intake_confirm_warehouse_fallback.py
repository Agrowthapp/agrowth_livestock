"""
Tests for BUG 1: confirm_intake fails with MandatoryError on warehouse
when the intake (typically a v9-migrated legacy intake) has no
`warehouse` populated.

The seam under test:

  * `agrowth_livestock.api.intakes.confirm_intake` MUST defensively
    resolve a warehouse for the intake before calling the doc-level
    `doc.confirm_intake(...)`, so an intake with no warehouse does NOT
    crash with `MandatoryError: warehouse` on save.

  * The defensive resolution order is the same one
    `_create_herd_batch_for_intake` (PR2) and `_assign_animals_to_default_corral`
    already use for fallback (settlement warehouse, herd_batch warehouse,
    default Acostumbramiento corral), so the seam is consistent.

Why source-parse? Same discipline as
`test_intake_confirm_materialization.py` — the BFF CI does not have a
Frappe runtime, and `api/intakes.py` imports `frappe` at module load.
The runtime behavior of the fallback is exercised by bench smoke tests;
CI keeps the regression net tight by asserting the seam at source level.

Triangulation:

  * The seam is that `confirm_intake` (in `api/intakes.py`) reads a
    warehouse from one of the documented fallback sources when the doc
    has no `warehouse`, then assigns it to `doc.warehouse` and saves
    before calling `doc.confirm_intake(...)`.
  * The doc-level `LivestockIntake.confirm_intake` (in the doctype
    module) is a secondary defense: if `self.warehouse` is still empty
    when the herd batch activation runs, the doc method MUST attempt a
    default-resolution first instead of letting the MandatoryError
    propagate from Frappe model validation on `self.save(...)`.
"""

import os
import re
import textwrap
import unittest


TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
COMPANION_ROOT = os.path.dirname(TESTS_DIR)
INTAKE_API_PATH = os.path.join(COMPANION_ROOT, "api", "intakes.py")
INTAKE_DOCTYPE_PATH = os.path.join(
    COMPANION_ROOT,
    "livestock",
    "doctype",
    "livestock_intake",
    "livestock_intake.py",
)


def _read_source(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _extract_method_source(module_source, method_name):
    """
    Return the dedented source of `def <method_name>(self, ...)` (or the
    top-level `def <method_name>(...)` for module-level functions) plus
    its body. Tab handling: expand tabs to 4 spaces so the parser works
    on both tab and space indentation.
    """
    lines = module_source.splitlines()
    signature_re = re.compile(rf"^(\s*)def\s+{re.escape(method_name)}\s*\(")
    for idx, line in enumerate(lines):
        m = signature_re.match(line)
        if not m:
            continue
        indent = m.group(1)
        collected = [line]
        for next_line in lines[idx + 1:]:
            if not next_line.strip():
                collected.append(next_line)
                continue
            expanded = next_line.expandtabs(4)
            next_indent_len = len(expanded) - len(expanded.lstrip(" "))
            expanded_indent = indent.expandtabs(4)
            if next_indent_len <= len(expanded_indent) and next_line.strip():
                break
            collected.append(next_line)
        return textwrap.dedent("\n".join(collected))
    return ""


class ApiConfirmIntakeWarehouseFallbackTests(unittest.TestCase):
    """The API-level `confirm_intake` MUST defensively resolve a warehouse
    when the intake has no `warehouse` populated, so the doc-level
    `doc.confirm_intake(...)` call does not raise `MandatoryError` from
    Frappe model validation.

    The resolution order (any of these is acceptable — the seam is that
    the API looks at least at ONE fallback source before saving):

      1. A `warehouse` kwarg passed in the request (declared as a
         parameter on the API function).
      2. The linked settlement's `warehouse` (intake.settlement).
      3. The linked herd_batch's `warehouse` (intake.herd_batch).
      4. A default Acostumbramiento corral (resolved via
         `_resolve_default_acostumbramiento_corral`).
    """

    def setUp(self):
        self.mod_src = _read_source(INTAKE_API_PATH)
        self.fn_src = _extract_method_source(self.mod_src, "confirm_intake")

    def test_api_confirm_intake_function_defined(self):
        self.assertTrue(
            self.fn_src.strip(),
            "api/intakes.py MUST define a `confirm_intake` function",
        )
        # The function MUST accept `warehouse` as a kwarg so the BFF can
        # pass a fallback warehouse explicitly when the doc has none.
        self.assertIn(
            "warehouse",
            self.fn_src,
            "api/intakes.py:confirm_intake MUST accept a `warehouse` kwarg "
            "so the BFF can pass a fallback when the doc has no warehouse",
        )

    def test_api_confirm_intake_propagates_warehouse_from_kwargs_or_fallback(self):
        """The seam: the API MUST assign `doc.warehouse` to a resolved
        value (from the kwarg, settlement, herd_batch, or default corral)
        before calling `doc.confirm_intake(...)` when `doc.warehouse` is
        empty."""
        # The simplest seam pattern is a `if not doc.warehouse:` block
        # that resolves a fallback and assigns it before save. We accept
        # any of these assignment patterns.
        has_guard = bool(
            re.search(
                r"if\s+not\s+doc\.warehouse",
                self.fn_src,
            )
        )
        # The function MUST call `doc.save(...)` (or `doc.db_set(...)`)
        # after assigning the fallback warehouse, so the field is
        # persisted before `doc.confirm_intake(...)` runs.
        has_save_after = bool(
            re.search(
                r"doc\.save\s*\(|doc\.db_set\s*\(",
                self.fn_src,
            )
        )
        self.assertTrue(
            has_guard,
            "api/intakes.py:confirm_intake MUST guard `if not doc.warehouse:` "
            "and resolve a fallback from settlement/herd_batch/default corral "
            "before calling doc.confirm_intake(...). Without this, a "
            "v9-migrated intake with no warehouse crashes with MandatoryError.",
        )
        self.assertTrue(
            has_save_after,
            "api/intakes.py:confirm_intake MUST persist the resolved warehouse "
            "(via doc.save() or doc.db_set()) before calling doc.confirm_intake(...) "
            "so the doc-level validation does not reject the save",
        )

    def test_api_confirm_intake_uses_settlement_or_herd_batch_or_corral(self):
        """The fallback resolution MUST consult at least one of: the
        linked settlement, the linked herd_batch, or the default
        Acostumbramiento corral resolver. A hardcoded `return None`
        fallback would pass the guard test but not this one."""
        # We assert that the function references at least one of the
        # documented fallback sources.
        sources = (
            "doc.settlement",                          # intake has a settlement pointer
            "doc.herd_batch",                          # intake has a herd_batch pointer
            "Livestock Settlement",                    # settlement lookup
            "Acostumbramiento",                        # default corral resolver
        )
        any_source = any(s in self.fn_src for s in sources)
        self.assertTrue(
            any_source,
            "api/intakes.py:confirm_intake MUST consult at least one fallback "
            "source (settlement, herd_batch, or default corral) when resolving "
            "a warehouse for an intake with no warehouse. Found none of: "
            + ", ".join(sources),
        )


class DocConfirmIntakeWarehouseGuardTests(unittest.TestCase):
    """Secondary defense: the doc-level `LivestockIntake.confirm_intake`
    MUST also guard against an empty `self.warehouse` before the herd
    batch activation step runs, so a defensive resolution is in place
    even when the API layer is bypassed (e.g. a direct Frappe form
    submission or a custom script).

    The seam: the doc method MUST attempt to resolve a default
    Acostumbramiento corral (or equivalent) and assign it to
    `self.warehouse` before the herd batch creation step.
    """

    def setUp(self):
        self.mod_src = _read_source(INTAKE_DOCTYPE_PATH)
        self.fn_src = _extract_method_source(self.mod_src, "confirm_intake")

    def test_doc_confirm_intake_guards_against_empty_warehouse(self):
        """The doc-level confirm_intake MUST check `if not self.warehouse:`
        (or equivalent) and attempt a default resolution before the
        herd batch creation step throws. This is the second line of
        defense behind the API-layer fix."""
        # Accept any of: explicit guard, helper call, or early return.
        has_guard = bool(
            re.search(
                r"if\s+not\s+self\.warehouse",
                self.fn_src,
            )
        )
        has_default_resolver = (
            "_resolve_default_acostumbramiento_corral" in self.fn_src
        )
        self.assertTrue(
            has_guard or has_default_resolver,
            "livestock_intake.py:confirm_intake MUST guard `if not self.warehouse:` "
            "or call `_resolve_default_acostumbramiento_corral` so a v9-migrated "
            "intake with no warehouse does not crash before the API can save. "
            "Without this defense, direct Frappe form submissions still hit the "
            "MandatoryError on save.",
        )


if __name__ == "__main__":
    unittest.main()
