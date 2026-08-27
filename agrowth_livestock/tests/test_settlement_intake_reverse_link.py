"""
Tests for the Livestock Settlement reverse `intake` link
(livestock-entry-settlement-boundary PR2 review fix).

Covers the BLOCKER finding: when settlement.on_submit() creates a Livestock
Intake and sets `intake.settlement = self.name`, the reverse link
`self.intake = intake.name` must also be set so the settlement row has the
canonical join key. The `Livestock Settlement` DocType MUST declare the
`intake` field (Link to `Livestock Intake`, read-only) for the reverse
link to persist.

The two Frappe doctype trees (`agrowth_livestock/doctype/...` and
`agrowth_livestock/livestock/doctype/...`) MUST stay symmetric so the seam
cannot drift. We assert, by source-parse + JSON-parse, that:

  1. Both `livestock_settlement.json` files declare the `intake` field as a
     Link to `Livestock Intake`, marked read-only (the settlement never
     authors the intake; the intake owns its own status transitions).
  2. Both `livestock_settlement.py` files set `self.intake = intake.name`
     after `intake.insert()` inside `create_livestock_intake()`.

Source-parse because the BFF CI does not have a Frappe runtime, and the
modules unconditionally `import frappe` at module-load time. Line-scanning
inside a single method body is the cheapest, most honest evidence that
the reverse-link contract holds.
"""

import json
import os
import re
import textwrap
import unittest


TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
COMPANION_ROOT = os.path.dirname(TESTS_DIR)

TOP_TREE_PATH = os.path.join(
    COMPANION_ROOT,
    "doctype",
    "livestock_settlement",
    "livestock_settlement.py",
)
TOP_TREE_JSON_PATH = os.path.join(
    COMPANION_ROOT,
    "doctype",
    "livestock_settlement",
    "livestock_settlement.json",
)
NESTED_TREE_PATH = os.path.join(
    COMPANION_ROOT,
    "livestock",
    "doctype",
    "livestock_settlement",
    "livestock_settlement.py",
)
NESTED_TREE_JSON_PATH = os.path.join(
    COMPANION_ROOT,
    "livestock",
    "doctype",
    "livestock_settlement",
    "livestock_settlement.json",
)


def _read_source(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _extract_method_source(module_source, method_name):
    """Return the dedented source of `def <method_name>(self, ...)` plus its
    indented body. Mirrors the helper used by
    test_settlement_on_submit_boundary so the parser contract is consistent.
    """
    lines = module_source.splitlines()
    signature_re = re.compile(rf"^(\s*)def\s+{re.escape(method_name)}\s*\(self")
    for idx, line in enumerate(lines):
        m = signature_re.match(line)
        if not m:
            continue
        indent = m.group(1)
        body_indent = indent + "    "
        collected = [line]
        for next_line in lines[idx + 1:]:
            if not next_line.strip():
                collected.append(next_line)
                continue
            next_indent_len = len(next_line) - len(next_line.lstrip(" "))
            if next_indent_len <= len(indent) and next_line.strip():
                break
            collected.append(next_line)
        return textwrap.dedent("\n".join(collected))
    return ""


class IntakeReverseLinkJsonTests(unittest.TestCase):
    """The Livestock Settlement DocType MUST declare `intake` as a read-only
    Link to Livestock Intake in BOTH trees so the reverse link persists.
    """

    def _assert_intake_field_present(self, json_doc, tree_label):
        fields = json_doc.get("fields", [])
        intake_fields = [f for f in fields if f.get("fieldname") == "intake"]
        self.assertEqual(
            len(intake_fields),
            1,
            f"{tree_label}: Livestock Settlement JSON must declare exactly one "
            f"`intake` field, found {len(intake_fields)}",
        )
        field = intake_fields[0]
        self.assertEqual(
            field.get("fieldtype"),
            "Link",
            f"{tree_label}: `intake` must be a Link field",
        )
        self.assertEqual(
            field.get("options"),
            "Livestock Intake",
            f"{tree_label}: `intake` must point to Livestock Intake",
        )
        # Read-only because the settlement never authors the intake; the
        # intake owns its own status transitions. The reverse link is a
        # bookkeeping pointer set by the settlement's create_livestock_intake.
        self.assertEqual(
            field.get("read_only"),
            1,
            f"{tree_label}: `intake` must be read-only (intake owns the link)",
        )

    def test_top_tree_json_declares_intake_link(self):
        doc = _read_json(TOP_TREE_JSON_PATH)
        self._assert_intake_field_present(doc, "Top tree")

    def test_nested_tree_json_declares_intake_link(self):
        doc = _read_json(NESTED_TREE_JSON_PATH)
        self._assert_intake_field_present(doc, "Nested tree")


class IntakeReverseLinkPythonTests(unittest.TestCase):
    """The settlement's `create_livestock_intake()` MUST set
    `self.intake = intake.name` after `intake.insert()` so the reverse link
    persists. Both trees must implement this symmetrically.
    """

    def _assert_reverse_link_set(self, module_source, tree_label):
        method_src = _extract_method_source(
            module_source, "create_livestock_intake"
        )
        self.assertTrue(
            method_src.strip(),
            f"{tree_label}: could not extract create_livestock_intake source",
        )
        # The settlement must call intake.insert(ignore_permissions=True)
        # AND then persist `intake.name` into `self.intake` so the link
        # survives. The settlement is mid-submission flow, so the canonical
        # way to write a field is `self.db_set("intake", intake.name, ...)`,
        # but a direct `self.intake = intake.name` (followed by save) is also
        # acceptable. We assert on the EFFECT (intake.name gets persisted into
        # the settlement's `intake` field) not on the implementation detail.
        self.assertIn(
            "intake.insert",
            method_src,
            f"{tree_label}: create_livestock_intake must call intake.insert()",
        )
        uses_db_set = re.search(
            r'self\.db_set\(\s*["\']intake["\']\s*,\s*intake\.name', method_src
        )
        uses_direct_assign = "self.intake = intake.name" in method_src
        self.assertTrue(
            bool(uses_db_set) or uses_direct_assign,
            f"{tree_label}: create_livestock_intake must persist the reverse "
            f"link by either `self.db_set(\"intake\", intake.name, ...)` or "
            f"`self.intake = intake.name` (followed by save)",
        )
        # And the persist call must come AFTER the insert (so intake.name exists).
        insert_pos = method_src.find("intake.insert")
        if uses_db_set:
            persist_pos = uses_db_set.start()
        else:
            persist_pos = method_src.find("self.intake = intake.name")
        self.assertGreater(
            persist_pos,
            insert_pos,
            f"{tree_label}: reverse-link persist MUST come after "
            f"`intake.insert()` so intake.name is populated",
        )

    def test_top_tree_sets_reverse_link(self):
        src = _read_source(TOP_TREE_PATH)
        self._assert_reverse_link_set(src, "Top tree")

    def test_nested_tree_sets_reverse_link(self):
        src = _read_source(NESTED_TREE_PATH)
        self._assert_reverse_link_set(src, "Nested tree")


class IntakeReverseLinkTreeSymmetryTests(unittest.TestCase):
    """Both trees MUST be in lock-step so the seam cannot drift."""

    def test_json_intake_field_matches_between_trees(self):
        top = _read_json(TOP_TREE_JSON_PATH)
        nested = _read_json(NESTED_TREE_JSON_PATH)
        top_intake = next(
            (f for f in top.get("fields", []) if f.get("fieldname") == "intake"),
            None,
        )
        nested_intake = next(
            (f for f in nested.get("fields", []) if f.get("fieldname") == "intake"),
            None,
        )
        self.assertIsNotNone(
            top_intake,
            "Top tree JSON missing `intake` field",
        )
        self.assertIsNotNone(
            nested_intake,
            "Nested tree JSON missing `intake` field",
        )
        # Compare the field-shape surface (ignore the metadata that can drift
        # legitimately: label, creation, modified).
        for key in ("fieldtype", "options", "read_only", "reqd"):
            self.assertEqual(
                top_intake.get(key),
                nested_intake.get(key),
                f"Top and nested tree `intake` field differ on key {key!r}: "
                f"{top_intake.get(key)!r} vs {nested_intake.get(key)!r}",
            )

    def test_python_create_livestock_intake_call_graph_matches(self):
        top_method = _extract_method_source(
            _read_source(TOP_TREE_PATH), "create_livestock_intake"
        )
        nested_method = _extract_method_source(
            _read_source(NESTED_TREE_PATH), "create_livestock_intake"
        )
        # Both must reference the same set of canonical symbols
        # (intake.insert, the reverse-link persist). We accept either form
        # of persist (db_set or direct assignment) so the test is robust to
        # legitimate implementation choices.
        top_persists = re.search(
            r'self\.db_set\(\s*["\']intake["\']\s*,\s*intake\.name', top_method
        ) or "self.intake = intake.name" in top_method
        nested_persists = re.search(
            r'self\.db_set\(\s*["\']intake["\']\s*,\s*intake\.name', nested_method
        ) or "self.intake = intake.name" in nested_method
        self.assertIn(
            "intake.insert",
            top_method,
            "Top tree create_livestock_intake missing 'intake.insert'",
        )
        self.assertIn(
            "intake.insert",
            nested_method,
            "Nested tree create_livestock_intake missing 'intake.insert'",
        )
        self.assertTrue(
            top_persists,
            "Top tree create_livestock_intake missing reverse-link persist",
        )
        self.assertTrue(
            nested_persists,
            "Nested tree create_livestock_intake missing reverse-link persist",
        )


if __name__ == "__main__":
    unittest.main()
