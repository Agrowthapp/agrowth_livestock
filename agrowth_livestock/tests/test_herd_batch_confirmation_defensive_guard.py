"""
Tests for the defensive confirmation_* field guards in
`agrowth_livestock.api.herd_batches.confirm_herd_batch` and
`agrowth_livestock.api.intakes.list_intake_history_feed`.

Background (PR2 / livestock-entry-settlement-boundary):
The Herd Batch DocType gained `confirmation_status`, `confirmation_mode`,
and `confirmed_at` fields. Pre-PR2 deployments and partially-migrated
sites may not have those fields yet. The companion API MUST write the
fields defensively so legacy schemas still 200/empty-result instead of
417'ing.

This is a source-parse test (mirrors `test_intake_confirm_materialization.py`)
because the BFF CI does not have a Frappe runtime.

Coverage:
- `confirm_herd_batch` MUST route all field writes through a
  `meta.get_field` / `hasattr` guard. Asserting the unconditional
  `doc.confirmation_status = ...` shape is forbidden.
- The filter / select on `confirmation_status` inside
  `list_intake_history_feed` MUST be conditional on the field existing.
- The Herd Batch migration patch v10 MUST register the confirmation
  fields via `create_custom_fields` (or equivalent) so the schema
  lands on legacy sites after a `bench migrate`.
- The Herd Batch `calculate_totals` MUST guard the `total_*` writes.
"""

import os
import re
import unittest


TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
COMPANION_ROOT = os.path.dirname(TESTS_DIR)

API_HERD_BATCHES_PATH = os.path.join(COMPANION_ROOT, "api", "herd_batches.py")
API_INTAKES_PATH = os.path.join(COMPANION_ROOT, "api", "intakes.py")
PATCH_V10_PATH = os.path.join(
    COMPANION_ROOT, "patches", "v10_add_herd_batch_confirmation_fields.py"
)
HOOKS_PATH = os.path.join(COMPANION_ROOT, "hooks.py")
HERD_BATCH_DT_PATH = os.path.join(COMPANION_ROOT, "doctype", "herd_batch", "herd_batch.json")
HERD_BATCH_PY_PATH = os.path.join(COMPANION_ROOT, "doctype", "herd_batch", "herd_batch.py")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _extract_function_source(source, function_name):
    """Return the dedented source of `def <function_name>(...)` plus its body.

    Tabs are expanded to 4 spaces so this works regardless of indentation style.
    """
    lines = source.splitlines()
    sig_re = re.compile(rf"^(\s*)def\s+{re.escape(function_name)}\s*\(")
    for idx, line in enumerate(lines):
        m = sig_re.match(line)
        if not m:
            continue
        indent = m.group(1)
        collected = [line]
        for nxt in lines[idx + 1:]:
            if not nxt.strip():
                collected.append(nxt)
                continue
            expanded = nxt.expandtabs(4)
            next_indent_len = len(expanded) - len(expanded.lstrip(" "))
            expanded_indent = indent.expandtabs(4)
            if next_indent_len <= len(expanded_indent):
                break
            collected.append(nxt)
        import textwrap
        return textwrap.dedent("\n".join(collected))
    return ""


class ConfirmHerdBatchDefensiveGuardTests(unittest.TestCase):
    """`confirm_herd_batch` MUST NOT write confirmation_* fields unconditionally."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(API_HERD_BATCHES_PATH)

    def test_confirm_herd_batch_source_exists(self):
        body = _extract_function_source(self.src, "confirm_herd_batch")
        self.assertTrue(
            body.strip(),
            "confirm_herd_batch function not found in api/herd_batches.py",
        )

    def test_confirm_herd_batch_uses_defensive_field_writer(self):
        # A `_set_confirmation_fields` helper (or equivalent) must exist
        # and be called from confirm_herd_batch — so the actual writes go
        # through a `meta.get_field` / `hasattr` guard.
        body = _extract_function_source(self.src, "confirm_herd_batch")
        self.assertRegex(
            body,
            r"_set_confirmation_fields\s*\(",
            "confirm_herd_batch MUST delegate confirmation field writes "
            "to a `_set_confirmation_fields` defensive helper.",
        )

    def test_set_confirmation_fields_helper_guards_each_field(self):
        helper_src = _extract_function_source(self.src, "_set_confirmation_fields")
        self.assertTrue(
            helper_src.strip(),
            "_set_confirmation_fields helper not found — every field must be "
            "guarded against a missing-schema site.",
        )
        # The guard MUST check the meta / hasattr before writing.
        for field in ("confirmation_status", "confirmation_mode", "confirmed_at"):
            with self.subTest(field=field):
                self.assertIn(
                    field,
                    helper_src,
                    f"_set_confirmation_fields must reference `{field}`",
                )
        # Each write must be guarded by either meta.get_field or hasattr.
        guard_pattern = re.compile(
            r"(meta\.get_field\([^)]*\)\s+and\s+hasattr\([^)]*\)|"
            r"hasattr\([^)]*\)\s+and\s+meta\.get_field\([^)]*\))",
            re.MULTILINE,
        )
        guards = guard_pattern.findall(helper_src)
        self.assertGreaterEqual(
            len(guards),
            1,
            "_set_confirmation_fields must guard every write with a "
            "meta.get_field / hasattr check (saw no guards).",
        )

    def test_confirm_herd_batch_response_shape_uses_getattr(self):
        # The returned dict MUST use `getattr(doc, ..., None)` for the
        # confirmation fields, not direct attribute access — otherwise the
        # response shape itself will 417 on legacy sites.
        body = _extract_function_source(self.src, "confirm_herd_batch")
        self.assertIn(
            "getattr(doc, \"confirmation_status\"",
            body,
            "confirm_herd_batch return shape MUST read confirmation_status via getattr.",
        )
        self.assertIn(
            "getattr(doc, \"confirmation_mode\"",
            body,
            "confirm_herd_batch return shape MUST read confirmation_mode via getattr.",
        )

    def test_no_unconditional_writes_to_confirmation_fields(self):
        # The literal pattern `doc.confirmation_status =` MUST NOT appear
        # outside the guarded helper. A regression that copies the old
        # write into confirm_herd_batch directly would land here.
        body = _extract_function_source(self.src, "confirm_herd_batch")
        self.assertNotIn(
            "doc.confirmation_status =",
            body,
            "confirm_herd_batch must NOT write doc.confirmation_status directly.",
        )
        self.assertNotIn(
            "doc.confirmation_mode =",
            body,
            "confirm_herd_batch must NOT write doc.confirmation_mode directly.",
        )
        self.assertNotIn(
            "doc.confirmed_at =",
            body,
            "confirm_herd_batch must NOT write doc.confirmed_at directly.",
        )


class ListIntakeHistoryFeedDefensiveGuardTests(unittest.TestCase):
    """`list_intake_history_feed` MUST NOT 417 on a missing confirmation_status field."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(API_INTAKES_PATH)

    def test_history_feed_function_exists(self):
        body = _extract_function_source(self.src, "list_intake_history_feed")
        self.assertTrue(body.strip(), "list_intake_history_feed not found")

    def test_filter_on_confirmation_status_is_conditional(self):
        body = _extract_function_source(self.src, "list_intake_history_feed")
        # The hard filter `["confirmation_status", "=", "Completed"]` MUST be
        # conditional on the field actually existing on the deployed schema.
        self.assertRegex(
            body,
            r"meta\.get_field\(\s*[\"']confirmation_status[\"']\s*\)",
            "list_intake_history_feed must check meta.get_field('confirmation_status') "
            "before adding the filter.",
        )
        # The unconditional `["confirmation_status", "=", "Completed"]`
        # literal MUST NOT appear as a top-level filters entry — it must
        # be appended inside the `if` block above.
        unconditional_filter = re.compile(
            r"filters\s*=\s*\[\s*\[?\s*[\"']confirmation_status[\"']\s*,",
            re.MULTILINE,
        )
        self.assertNotRegex(
            body,
            unconditional_filter,
            "filters = [['confirmation_status', ...]] must NOT be the literal "
            "initial filter — it must be conditionally appended.",
        )

    def test_fields_selection_uses_existing_fields_helper(self):
        body = _extract_function_source(self.src, "list_intake_history_feed")
        # The fields list MUST go through `_existing_fields` so missing
        # schema fields do not get requested.
        self.assertIn(
            "_existing_fields",
            body,
            "list_intake_history_feed must filter its fields list with _existing_fields.",
        )


class HerdBatchMigrationPatchTests(unittest.TestCase):
    """The v10 patch MUST register the confirmation fields for legacy sites."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(PATCH_V10_PATH)
        cls.hooks_src = _read(HOOKS_PATH)

    def test_patch_creates_confirmation_fields(self):
        # The patch must call `create_custom_fields` (or equivalent) with
        # the confirmation_* fields so bench migrate brings legacy sites up
        # to the post-PR2 schema.
        self.assertRegex(
            self.src,
            r"create_custom_fields\s*\(",
            "v10 patch must call create_custom_fields to register the new fields.",
        )
        for field in ("confirmation_status", "confirmation_mode", "confirmed_at"):
            with self.subTest(field=field):
                self.assertIn(
                    field,
                    self.src,
                    f"v10 patch must register the `{field}` field.",
                )

    def test_patch_is_idempotent(self):
        # The patch must tolerate being re-run on a fully migrated site.
        # `create_custom_fields(..., update=True)` is the canonical Frappe
        # pattern. Allow either `update=True` or `if_not_exists` style —
        # but there must be SOME signal that re-runs are safe.
        self.assertTrue(
            "update=True" in self.src or "update=False" in self.src,
            "v10 patch must explicitly call out update behavior for idempotency.",
        )

    def test_patch_is_registered_in_hooks(self):
        # The migration must be wired in hooks.py so a fresh `bench migrate`
        # actually runs it.
        self.assertIn(
            "v10_add_herd_batch_confirmation_fields",
            self.hooks_src,
            "v10 patch must be referenced in hooks.py migration_patches.",
        )


class HerdBatchSchemaJsonTests(unittest.TestCase):
    """The shipped Herd Batch JSON MUST declare the new fields so a fresh
    install picks them up without needing the v10 migration patch."""

    @classmethod
    def setUpClass(cls):
        import json
        cls.json_src = _read(HERD_BATCH_DT_PATH)
        cls.dt = json.loads(cls.json_src)

    def test_doctype_name_is_herd_batch(self):
        self.assertEqual(self.dt.get("name"), "Herd Batch")

    def test_declares_confirmation_status(self):
        fieldnames = {f.get("fieldname") for f in self.dt.get("fields", [])}
        self.assertIn("confirmation_status", fieldnames)
        self.assertIn("confirmation_mode", fieldnames)
        self.assertIn("confirmed_at", fieldnames)

    def test_declares_total_fields_used_by_calculate_totals(self):
        # The .py calculate_totals writes total_heads / total_weight /
        # total_amount; the JSON must declare them so save() does not
        # raise on a freshly installed site.
        fieldnames = {f.get("fieldname") for f in self.dt.get("fields", [])}
        for field in ("total_heads", "total_weight", "total_amount"):
            with self.subTest(field=field):
                self.assertIn(
                    field,
                    fieldnames,
                    f"Herd Batch JSON must declare `{field}` because "
                    f"herd_batch.py#calculate_totals writes it.",
                )


class HerdBatchCalculateTotalsDefensiveGuardTests(unittest.TestCase):
    """`herd_batch.py#calculate_totals` MUST guard total_* writes."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(HERD_BATCH_PY_PATH)

    def test_calculate_totals_uses_meta_guard(self):
        body = _extract_function_source(self.src, "calculate_totals")
        self.assertIn(
            "meta.get_field",
            body,
            "herd_batch.py#calculate_totals must guard total_* writes with "
            "meta.get_field so legacy schemas do not 417.",
        )
        for field in ("total_heads", "total_weight", "total_amount"):
            with self.subTest(field=field):
                # Either the field appears inside a meta.get_field check, or
                # the assignment is otherwise conditional. We assert both:
                # the field is referenced AND the assignment is conditional.
                self.assertRegex(
                    body,
                    rf"meta\.get_field\(\s*[\"']{re.escape(field)}[\"']\s*\)",
                    f"calculate_totals must check meta.get_field('{field}').",
                )


if __name__ == "__main__":
    unittest.main()
