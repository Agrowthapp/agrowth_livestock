"""
Tests for the intake-owned Stock Entry persistence + revert seam
(livestock-entry-settlement-boundary PR 2 — companion Frappe review-fix).

Covers the 2 PR 2 review findings the dual review surfaced for the
intake-owned materialization path:

  1. **HIGH** — `Livestock Intake` doctype JSON MUST declare a
     `stock_entry` Link field (Stock Entry, read-only) so the canonical
     post-PR2 path can persist the pointer. Without the field, the
     `db_set("stock_entry", ...)` call in `_create_and_submit_stock_entry`
     is a no-op and the BFF cannot surface the Stock Entry on the
     intake DTO. The revert path is also broken: the intake-owned Stock
     Entry stays submitted because `_cancel_settlement_stock_entry` only
     consults `settlement.stock_entry` (None for post-PR2 intakes).

  2. **MEDIUM (safe)** — `_create_herd_batch_for_intake` docstring says
     `origin_type = "Livestock Intake"` but the code hardcodes `"Other"`.
     The code must match the documented behavior so the operational
     track is the source of truth for the Herd Batch artifact (per
     design §Architecture Decisions).

Why source-parse? The BFF CI does not have a Frappe runtime, and the
intake module imports `frappe` at module-load time. Source-level
assertions on the doctype JSON and the module body are the cheapest,
most honest evidence that the contract holds. The runtime behavior
(actual `frappe.get_doc("Stock Entry").cancel()`) requires a bench to
exercise; the source-parse seam is what CI can pin.

Sibling assertion surfaces (kept in lock-step):

- `test_intake_confirm_materialization.py` covers the create + submit
  call graph (intake owns the materialization).
- `test_settlement_intake_reverse_link.py` covers the settlement-side
  reverse link from F.1 review-fix.
- This file covers the post-PR2 Stock Entry persistence + revert seam.
"""

import json
import os
import re
import textwrap
import unittest


# Repo-relative path: the intake lives only in the nested tree.
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
COMPANION_ROOT = os.path.dirname(TESTS_DIR)
INTAKE_DIR = os.path.join(
    COMPANION_ROOT,
    "livestock",
    "doctype",
    "livestock_intake",
)
INTAKE_JSON_PATH = os.path.join(INTAKE_DIR, "livestock_intake.json")
INTAKE_PY_PATH = os.path.join(INTAKE_DIR, "livestock_intake.py")


def _read_source(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _extract_method_source(module_source, method_name):
    """
    Return the dedented source of `def <method_name>(self, ...)` plus its
    indented body. Mirrors the helper used in
    `test_intake_confirm_materialization.py` so the parsing discipline is
    consistent across the test suite.

    Tab handling: the intake module uses tab indentation. We expand
    tabs to 4 spaces before measuring indentation so the parser works
    correctly.
    """
    lines = module_source.splitlines()
    signature_re = re.compile(rf"^(\s*)def\s+{re.escape(method_name)}\s*\(self")
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


def _intake_source():
    return _read_source(INTAKE_PY_PATH)


def _intake_json():
    return _read_json(INTAKE_JSON_PATH)


# Methods whose body must reference the intake-owned stock_entry seam.
REVERT_HELPER = "_cancel_settlement_stock_entry"
CREATE_HELPER = "_create_and_submit_stock_entry"


class IntakeStockEntryFieldTests(unittest.TestCase):
    """The `Livestock Intake` doctype JSON MUST declare a `stock_entry`
    Link field so the canonical post-PR2 path can persist the pointer.
    """

    def setUp(self):
        self.doctype_json = _intake_json()
        self.fields = self.doctype_json.get("fields", [])
        self.stock_entry_fields = [
            f for f in self.fields if f.get("fieldname") == "stock_entry"
        ]
        self.field_order = self.doctype_json.get("field_order", [])

    def test_livestock_intake_json_declares_stock_entry_field(self):
        """RED for finding 1 — the field is missing in the working tree."""
        self.assertEqual(
            len(self.stock_entry_fields),
            1,
            "Livestock Intake doctype JSON MUST declare exactly one "
            "`stock_entry` field (the canonical seam for the post-PR2 "
            "Stock Entry pointer). Found: "
            f"{[f.get('fieldname') for f in self.fields]}",
        )

    def test_stock_entry_field_is_link_to_stock_entry(self):
        """The field MUST be a Link to Stock Entry so the BFF can read it."""
        self.assertEqual(
            len(self.stock_entry_fields),
            1,
            "stock_entry field missing — see test_livestock_intake_json_declares_stock_entry_field",
        )
        se_field = self.stock_entry_fields[0]
        self.assertEqual(
            se_field.get("fieldtype"),
            "Link",
            f"stock_entry MUST be a Link field. Got: {se_field.get('fieldtype')!r}",
        )
        self.assertEqual(
            se_field.get("options"),
            "Stock Entry",
            f"stock_entry MUST link to Stock Entry. Got: {se_field.get('options')!r}",
        )

    def test_stock_entry_field_is_read_only(self):
        """The field MUST be read-only so the operational track owns the
        value and the BFF cannot write through the JSON."""
        self.assertEqual(
            len(self.stock_entry_fields),
            1,
            "stock_entry field missing — see test_livestock_intake_json_declares_stock_entry_field",
        )
        se_field = self.stock_entry_fields[0]
        self.assertTrue(
            se_field.get("read_only"),
            "stock_entry MUST be read-only (the intake owns the value "
            "via db_set; users MUST NOT edit it through the form).",
        )

    def test_stock_entry_field_has_human_label(self):
        """The field MUST have a label so it renders on the form."""
        self.assertEqual(
            len(self.stock_entry_fields),
            1,
            "stock_entry field missing — see test_livestock_intake_json_declares_stock_entry_field",
        )
        se_field = self.stock_entry_fields[0]
        self.assertTrue(
            se_field.get("label"),
            "stock_entry MUST have a human label so it renders on the "
            "Livestock Intake form.",
        )

    def test_stock_entry_in_field_order(self):
        """The field MUST be in field_order so it renders in the form
        layout. A field declared in `fields` but missing from
        `field_order` is unreachable from the form."""
        self.assertEqual(
            len(self.stock_entry_fields),
            1,
            "stock_entry field missing — see test_livestock_intake_json_declares_stock_entry_field",
        )
        self.assertIn(
            "stock_entry",
            self.field_order,
            "stock_entry MUST be in field_order so it renders in the "
            "Livestock Intake form layout.",
        )

    def test_doctype_name_is_livestock_intake(self):
        """Sanity guard: the JSON we're parsing is actually the Livestock
        Intake doctype. A test that ran against the wrong JSON would
        give a false green."""
        self.assertEqual(
            self.doctype_json.get("name"),
            "Livestock Intake",
            "Sanity guard failed: parsed JSON is not the Livestock Intake doctype",
        )


class IntakeStockEntryPersistenceTests(unittest.TestCase):
    """The `_create_and_submit_stock_entry` method MUST persist the Stock
    Entry name on the intake row (via `db_set` or assignment to
    `self.stock_entry`) once the doctype JSON declares the field. Without
    the field, the `hasattr(self, "stock_entry")` guard is always False
    and the pointer is lost.
    """

    def setUp(self):
        self.mod_src = _intake_source()
        self.method_src = _extract_method_source(self.mod_src, CREATE_HELPER)

    def test_method_source_is_non_empty(self):
        self.assertTrue(
            self.method_src.strip(),
            f"{CREATE_HELPER} method is empty or not found",
        )

    def test_persists_intake_pointer_via_db_set(self):
        """The new method MUST persist `self.stock_entry` via `db_set`
        so the BFF can read the Stock Entry name on the intake DTO."""
        has_db_set = (
            'db_set("stock_entry"' in self.method_src
            or "db_set('stock_entry'" in self.method_src
        )
        self.assertTrue(
            has_db_set,
            f"{CREATE_HELPER} MUST persist self.stock_entry via db_set "
            "so the BFF can read the Stock Entry name on the intake DTO. "
            "The PR 2 review fix requires the seam to be committed, not just set in memory.",
        )

    def test_does_not_use_hasattr_guard_for_stock_entry(self):
        """Triangulation: with the field declared in the doctype JSON,
        the `hasattr(self, "stock_entry")` guard is dead code (it is
        always True). The implementation MUST drop the guard so the
        `db_set` call is unconditional — that's how the seam is wired
        for the BFF. A `hasattr`-guarded `db_set` would still work today
        but is a smell that signals the field is optional, which the
        design forbids (the intake OWNS the Stock Entry post-PR2)."""
        # Acceptable patterns:
        #   - direct `self.db_set("stock_entry", ...)` (no hasattr guard)
        #   - direct `self.stock_entry = ...` + db_set (no hasattr guard)
        # Forbidden patterns:
        #   - `if hasattr(self, "stock_entry"): self.db_set(...)` — dead
        #     guard now that the field is declared
        lines = self.method_src.splitlines()
        for line in lines:
            stripped = line.strip()
            if "hasattr" in stripped and "stock_entry" in stripped:
                self.fail(
                    f"{CREATE_HELPER} still gates the db_set with "
                    f"`hasattr(self, 'stock_entry')`. The field is now "
                    f"declared on the doctype, so the guard is dead code "
                    f"and signals the field is optional. Drop the guard. "
                    f"Offending line: {stripped!r}"
                )


class IntakeStockEntryRevertTests(unittest.TestCase):
    """`revert_intake` MUST cancel the intake-owned Stock Entry when no
    settlement-owned Stock Entry exists. Without this branch, the
    post-PR2 path leaves the Stock Entry submitted (and the stock ledger
    entries posted) when an operator reverts the intake.
    """

    def setUp(self):
        self.mod_src = _intake_source()
        self.revert_src = _extract_method_source(self.mod_src, "revert_intake")
        self.cancel_src = _extract_method_source(self.mod_src, REVERT_HELPER)
        self.create_src = _extract_method_source(self.mod_src, CREATE_HELPER)

    def test_revert_calls_cancel_helper(self):
        """The revert_intake call graph MUST still route through the
        cancel helper (the seam stays in place)."""
        self.assertTrue(
            self.revert_src.strip(),
            "revert_intake method is empty or not found",
        )
        self.assertIn(
            REVERT_HELPER,
            self.revert_src,
            f"revert_intake MUST call {REVERT_HELPER} so the Stock Entry "
            "is cancelled (settlement-owned OR intake-owned) before the "
            "intake status flips to Revertido.",
        )

    def test_cancel_helper_handles_intake_owned_stock_entry(self):
        """The cancel helper MUST consult `self.stock_entry` (intake-owned)
        as a fallback when the settlement-owned Stock Entry is absent.

        Triangulation: a hardcoded implementation that only consults
        `settlement.stock_entry` would fail this test and would leave
        the post-PR2 Stock Entry submitted after revert — the exact bug
        the PR 2 review found.
        """
        self.assertTrue(
            self.cancel_src.strip(),
            f"{REVERT_HELPER} method is empty or not found",
        )
        has_self_stock_entry = "self.stock_entry" in self.cancel_src
        self.assertTrue(
            has_self_stock_entry,
            f"{REVERT_HELPER} MUST consult `self.stock_entry` "
            "(intake-owned pointer) as a fallback when the settlement-"
            "owned Stock Entry is absent. Without this branch, the "
            "post-PR2 Stock Entry stays submitted after revert. "
            f"Current body:\n{self.cancel_src}",
        )

    def test_cancel_helper_cancels_intake_owned_stock_entry(self):
        """The cancel helper MUST call `.cancel()` on the intake-owned
        Stock Entry so the stock ledger entries are reversed. A
        hardcoded implementation that only set the field to None (or
        only read it) would fail this test."""
        self.assertTrue(
            self.cancel_src.strip(),
            f"{REVERT_HELPER} method is empty or not found",
        )
        # The seam is that the helper body references `self.stock_entry`
        # AND calls `.cancel()` on the resulting Stock Entry. A pure
        # `if self.stock_entry: return` would fail this test.
        has_self_ref = "self.stock_entry" in self.cancel_src
        has_cancel_call = bool(re.search(r"\.cancel\s*\(", self.cancel_src))
        self.assertTrue(
            has_self_ref and has_cancel_call,
            f"{REVERT_HELPER} MUST cancel the intake-owned Stock Entry "
            "(self.stock_entry). Found self.stock_entry ref: "
            f"{has_self_ref}, .cancel() call: {has_cancel_call}. "
            f"Current body:\n{self.cancel_src}",
        )

    def test_cancel_helper_does_not_lose_settlement_branch(self):
        """Tree-symmetry guard: the existing settlement-owned branch
        MUST remain reachable. The new intake-owned branch is an
        additional fallback, not a replacement."""
        self.assertTrue(
            self.cancel_src.strip(),
            f"{REVERT_HELPER} method is empty or not found",
        )
        # The existing settlement branch uses `self.settlement` and
        # `settlement.stock_entry`. The new branch uses `self.stock_entry`.
        # Both must be present in the body.
        has_settlement_branch = (
            "self.settlement" in self.cancel_src
            and "settlement.stock_entry" in self.cancel_src
        )
        has_intake_branch = "self.stock_entry" in self.cancel_src
        self.assertTrue(
            has_settlement_branch,
            f"{REVERT_HELPER} MUST keep the settlement-owned branch "
            "(self.settlement + settlement.stock_entry) so legacy "
            "intakes still revert correctly. Found: "
            f"settlement branch={has_settlement_branch}, intake branch={has_intake_branch}",
        )
        self.assertTrue(
            has_intake_branch,
            f"{REVERT_HELPER} MUST add the intake-owned branch "
            "(self.stock_entry) as a fallback. Found: "
            f"settlement branch={has_settlement_branch}, intake branch={has_intake_branch}",
        )

    def test_create_helper_persists_self_stock_entry(self):
        """Triangulation: the create helper MUST persist `self.stock_entry`
        (intake-owned) so the revert helper has something to cancel.
        Without persistence, the new revert branch is a no-op (always
        finds `self.stock_entry` empty)."""
        self.assertTrue(
            self.create_src.strip(),
            f"{CREATE_HELPER} method is empty or not found",
        )
        has_db_set = (
            'db_set("stock_entry"' in self.create_src
            or "db_set('stock_entry'" in self.create_src
        )
        self.assertTrue(
            has_db_set,
            f"{CREATE_HELPER} MUST persist self.stock_entry via db_set "
            "so the revert helper has a Stock Entry to cancel. "
            f"Current body:\n{self.create_src}",
        )


class IntakeHerdBatchOriginTypeTests(unittest.TestCase):
    """`_create_herd_batch_for_intake` MUST set `origin_type` to
    `"Livestock Intake"` (matching the docstring) so the operational
    track is the source of truth for the Herd Batch artifact. The code
    currently hardcodes `"Other"` which contradicts the docstring.
    """

    def setUp(self):
        self.mod_src = _intake_source()
        self.method_src = _extract_method_source(self.mod_src, "_create_herd_batch_for_intake")

    def test_method_source_is_non_empty(self):
        self.assertTrue(
            self.method_src.strip(),
            "_create_herd_batch_for_intake method is empty or not found",
        )

    def test_origin_type_uses_livestock_intake_literal(self):
        """RED for finding 2 — the implementation hardcodes `"Other"`
        while the docstring documents `"Livestock Intake"`."""
        # We assert the literal `"Livestock Intake"` (or single-quoted
        # equivalent) appears as the value of an `origin_type = ...`
        # assignment. We do NOT accept `"Other"` as the value.
        lines = self.method_src.splitlines()
        found_livestock_intake_literal = False
        found_other_literal_as_origin = False
        for line in lines:
            stripped = line.strip()
            # Skip comment-only lines and lines that are not assignments.
            if "origin_type" not in stripped or "=" not in stripped:
                continue
            if stripped.startswith("#"):
                continue
            value_side = stripped.split("=", 1)[1].strip()
            # Strip surrounding quotes for the comparison.
            if value_side in ('"Livestock Intake"', "'Livestock Intake'"):
                found_livestock_intake_literal = True
            elif value_side in ('"Other"', "'Other'"):
                found_other_literal_as_origin = True
        self.assertTrue(
            found_livestock_intake_literal,
            "_create_herd_batch_for_intake MUST set `origin_type` to "
            '"Livestock Intake" (per the docstring + design §Architecture '
            "Decisions: the operational track is the source of truth for "
            'the Herd Batch artifact). Found `origin_type` literals: '
            f"{[l.strip() for l in self.method_src.splitlines() if 'origin_type' in l]}",
        )
        self.assertFalse(
            found_other_literal_as_origin,
            "_create_herd_batch_for_intake MUST NOT set `origin_type` to "
            '"Other" — the docstring documents "Livestock Intake" and '
            "the design requires the operational track to be the source "
            "of truth. The current hardcode is a code/doc drift bug.",
        )

    def test_origin_type_assignment_does_not_use_self_settlement(self):
        """Triangulation: the origin_type MUST NOT be sourced from
        `self.settlement` (would re-couple the Herd Batch to the
        settlement, defeating the PR 2 boundary). The current code
        uses a hardcoded literal so this is a defensive assertion."""
        # The seam is that the assignment does NOT use self.settlement
        # as the value. A hardcoded "Livestock Intake" literal is
        # acceptable; self.settlement is not.
        lines = self.method_src.splitlines()
        for line in lines:
            stripped = line.strip()
            if (
                "origin_type" in stripped
                and "=" in stripped
                and not stripped.startswith("#")
            ):
                value_side = stripped.split("=", 1)[1].strip()
                self.assertNotEqual(
                    value_side, "self.settlement",
                    f"_create_herd_batch_for_intake MUST NOT set origin_type "
                    f"to self.settlement (would re-couple the Herd Batch to "
                    f"the settlement, defeating the PR 2 boundary). "
                    f"Offending line: {stripped!r}",
                )


if __name__ == "__main__":
    unittest.main()
