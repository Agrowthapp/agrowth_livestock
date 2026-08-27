"""
Tests for the Livestock Settlement on_submit boundary (livestock-entry-settlement-boundary PR1).

Covers task 1.1 + 1.3: the `on_submit` hook MUST only call `create_purchase_invoice`
and `create_livestock_intake`. It MUST NOT call `create_herd_batch` or
`create_stock_entry` — those are owned by the (future) intake confirm flow.

The two Frappe doctype trees (`agrowth_livestock/doctype/...` and
`agrowth_livestock/livestock/doctype/...`) MUST stay symmetric so the seam
cannot drift. We assert the on_submit call graph on each tree by parsing the
Python source from disk (no Frappe runtime) and inspecting the method body.

Why source-parse instead of importing? Because the BFF CI does not have a
Frappe runtime, and the modules unconditionally `import frappe` at module
load time. Source-level call-graph assertions are the cheapest, most honest
evidence that the contract holds and that the duplicate tree drift risk is
closed.
"""

import os
import re
import textwrap
import unittest


# Repo-relative paths from the companion repo root. The test runner is invoked
# with cwd = the companion repo, so we resolve relative to the file's
# grandparent (…/agrowth_livestock/tests/ → …/agrowth_livestock/).
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
COMPANION_ROOT = os.path.dirname(TESTS_DIR)

TOP_TREE_PATH = os.path.join(
    COMPANION_ROOT,
    "doctype",
    "livestock_settlement",
    "livestock_settlement.py",
)
NESTED_TREE_PATH = os.path.join(
    COMPANION_ROOT,
    "livestock",
    "doctype",
    "livestock_settlement",
    "livestock_settlement.py",
)


def _read_source(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _extract_method_source(module_source, method_name):
    """
    Return the dedented source of `def <method_name>(self, ...)` plus its
    indented body. We use a simple line-counting parser that scans forward
    from `def` until the indentation returns to the class level (or less).

    This is intentionally not `ast` because the source may use constructs
    that `ast` dislikes when modules are incomplete (e.g. unparsable
    `from frappe import _` imports). For our contract — call-graph inside a
    single `on_submit` body — line scanning is correct and robust.
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
            # Stop at a line whose indent is <= the method's own indent and
            # which is not blank and not a continuation.
            next_indent_len = len(next_line) - len(next_line.lstrip(" "))
            if next_indent_len <= len(indent) and next_line.strip():
                break
            collected.append(next_line)
        return textwrap.dedent("\n".join(collected))
    return ""


def _called_method_names(method_source):
    """
    Return the set of method calls of the form `self.foo()` found in the
    method source. We do NOT try to be a full Python parser — we just look
    for `self.<identifier>(` which is the canonical pattern used throughout
    the codebase.
    """
    return set(re.findall(r"self\.([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", method_source))


# Methods that PR1 FORBIDS calling from on_submit. The intake confirm flow
# (PR2) will own these; settlement stays strictly administrative.
FORBIDDEN_IN_ON_SUBMIT = frozenset({
    "create_herd_batch",
    "create_stock_entry",
})

# Methods that on_submit MUST call to satisfy the design (PI + pending intake).
REQUIRED_IN_ON_SUBMIT = frozenset({
    "create_purchase_invoice",
    "create_livestock_intake",
})


def _top_tree_source():
    return _read_source(TOP_TREE_PATH)


def _nested_tree_source():
    return _read_source(NESTED_TREE_PATH)


class OnSubmitBoundaryTopTreeTests(unittest.TestCase):
    """Top tree: agrowth_livestock/doctype/livestock_settlement/..."""

    def setUp(self):
        self.mod_src = _top_tree_source()
        self.src = _extract_method_source(self.mod_src, "on_submit")

    def test_module_source_is_non_empty(self):
        self.assertTrue(self.mod_src.strip(), "Top tree file is empty")

    def test_module_defines_livestock_settlement_class(self):
        self.assertIn(
            "class LivestockSettlement",
            self.mod_src,
            "Top tree module must define `LivestockSettlement` class",
        )

    def test_on_submit_source_is_non_empty(self):
        self.assertTrue(
            self.src.strip(),
            "Could not extract on_submit source from top tree",
        )

    def test_on_submit_calls_create_purchase_invoice(self):
        called = _called_method_names(self.src)
        self.assertIn(
            "create_purchase_invoice",
            called,
            "on_submit must create a Purchase Invoice",
        )

    def test_on_submit_calls_create_livestock_intake(self):
        called = _called_method_names(self.src)
        self.assertIn(
            "create_livestock_intake",
            called,
            "on_submit must create a pending Livestock Intake (new boundary)",
        )

    def test_on_submit_does_not_create_herd_batch(self):
        called = _called_method_names(self.src)
        self.assertNotIn(
            "create_herd_batch",
            called,
            "on_submit MUST NOT create a Herd Batch — intake confirm owns that",
        )

    def test_on_submit_does_not_create_stock_entry(self):
        called = _called_method_names(self.src)
        self.assertNotIn(
            "create_stock_entry",
            called,
            "on_submit MUST NOT create a Stock Entry — intake confirm owns that",
        )

    def test_on_submit_calls_exactly_the_required_methods(self):
        called = _called_method_names(self.src)
        # The full set of `self.<name>()` calls in on_submit MUST be a subset
        # of the required methods. A surprise new call (e.g. a future
        # `self.create_credit_note()`) would fail this test and force an
        # explicit design update.
        extras = called - REQUIRED_IN_ON_SUBMIT
        self.assertEqual(
            extras,
            set(),
            f"on_submit contains unexpected self.*() calls: {sorted(extras)}",
        )

    def test_on_submit_required_methods_are_all_present(self):
        called = _called_method_names(self.src)
        missing = REQUIRED_IN_ON_SUBMIT - called
        self.assertEqual(
            missing,
            set(),
            f"on_submit is missing required calls: {sorted(missing)}",
        )


class OnSubmitBoundaryNestedTreeTests(unittest.TestCase):
    """Nested tree: agrowth_livestock/livestock/doctype/livestock_settlement/..."""

    def setUp(self):
        self.mod_src = _nested_tree_source()
        self.src = _extract_method_source(self.mod_src, "on_submit")

    def test_module_defines_livestock_settlement_class(self):
        self.assertIn(
            "class LivestockSettlement",
            self.mod_src,
            "Nested tree module must define `LivestockSettlement` class",
        )

    def test_on_submit_does_not_create_herd_batch(self):
        called = _called_method_names(self.src)
        self.assertNotIn(
            "create_herd_batch",
            called,
            "Nested tree on_submit MUST NOT create a Herd Batch",
        )

    def test_on_submit_does_not_create_stock_entry(self):
        called = _called_method_names(self.src)
        self.assertNotIn(
            "create_stock_entry",
            called,
            "Nested tree on_submit MUST NOT create a Stock Entry",
        )

    def test_on_submit_calls_create_purchase_invoice(self):
        called = _called_method_names(self.src)
        self.assertIn("create_purchase_invoice", called)

    def test_on_submit_calls_create_livestock_intake(self):
        called = _called_method_names(self.src)
        self.assertIn("create_livestock_intake", called)

    def test_on_submit_calls_exactly_the_required_methods(self):
        called = _called_method_names(self.src)
        extras = called - REQUIRED_IN_ON_SUBMIT
        self.assertEqual(
            extras,
            set(),
            f"Nested tree on_submit contains unexpected self.*() calls: {sorted(extras)}",
        )


class TreeSymmetryTests(unittest.TestCase):
    """The two doctype trees must produce identical on_submit call graphs so the
    duplicate Frappe tree cannot drift (livestock-entry-settlement-boundary design
    §Architecture: 'Patch both trees symmetrically')."""

    def test_top_and_nested_on_submit_call_graphs_match(self):
        top_called = _called_method_names(
            _extract_method_source(_top_tree_source(), "on_submit")
        )
        nested_called = _called_method_names(
            _extract_method_source(_nested_tree_source(), "on_submit")
        )
        self.assertEqual(
            top_called,
            nested_called,
            (
                "on_submit call graph diverges between the two Frappe trees. "
                "Patch them symmetrically so the seam cannot drift."
            ),
        )


class CancelGuardIntegrationTests(unittest.TestCase):
    """Source-level evidence that both trees wire the cancel-guard helper into
    their on_cancel hook. We check that the import is present and that on_cancel
    calls the helper. This is the second TDD target of task 1.2 / 1.3."""

    def _assert_cancel_guard_wired(self, source):
        self.assertIn(
            "from agrowth_livestock.cancellation_policy import",
            source,
            "Settlement module must import the cancel-guard helper from "
            "agrowth_livestock.cancellation_policy",
        )
        on_cancel_src = _extract_method_source(source, "on_cancel")
        # The settlement may call the helper directly, or delegate to a
        # private wrapper (e.g. `_enforce_cancel_guard_for_linked_intake`)
        # which is the production pattern. Both prove the guard is wired.
        uses_helper_directly = (
            "resolve_intake_status_blocking_cancel" in on_cancel_src
        )
        uses_wrapper = (
            "_enforce_cancel_guard_for_linked_intake" in on_cancel_src
        )
        self.assertTrue(
            uses_helper_directly or uses_wrapper,
            "on_cancel must call the cancel-guard helper (directly or via a "
            "wrapper) before cancelling the PI",
        )
        # If the wrapper is used, it must itself invoke the helper so the
        # guard is not silently no-op.
        if uses_wrapper and not uses_helper_directly:
            wrapper_src = _extract_method_source(
                source, "_enforce_cancel_guard_for_linked_intake"
            )
            self.assertIn(
                "resolve_intake_status_blocking_cancel",
                wrapper_src,
                "_enforce_cancel_guard_for_linked_intake must call the policy helper",
            )

    def test_top_tree_wires_cancel_guard(self):
        self._assert_cancel_guard_wired(_top_tree_source())

    def test_nested_tree_wires_cancel_guard(self):
        self._assert_cancel_guard_wired(_nested_tree_source())


if __name__ == "__main__":
    unittest.main()
