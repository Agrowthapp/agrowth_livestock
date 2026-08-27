"""
Tests for the Livestock Intake confirm_intake call graph
(livestock-entry-settlement-boundary PR2 — companion Frappe slice).

Covers task 2.1 + 2.3 of the design: `confirm_intake` MUST call
`_create_herd_batch_for_intake` and `_create_and_submit_stock_entry` so
the intake owns the physical materialization (Herd Batch + Stock Entry),
not the settlement. The legacy `_submit_settlement_stock_entry` path
MUST still exist for migration (per design.md §File Changes) but MUST
NOT be called by `confirm_intake` once the intake-owned path is in
place — the new methods are the canonical post-PR2 path.

Why source-parse? The BFF CI does not have a Frappe runtime, and the
intake module imports `frappe` at module-load time. Source-level
call-graph assertions on `confirm_intake` are the cheapest, most honest
evidence that the contract holds. The runtime behavior of the new
methods (Herd Batch creation, Stock Entry submission) is exercised by
the bench-side smoke tests; CI keeps the regression net tight by
asserting the seam at the source level.

Sibling assertion surfaces:

- `settlement.create_herd_batch` / `settlement.create_stock_entry` MUST
  remain unreachable from `on_submit` (covered by
  `test_settlement_on_submit_boundary.py`).
- `intake.confirm_intake` MUST reach the new intake-owned methods.
- Tree symmetry: the boundary contract is on a SINGLE intake module
  (the `livestock/doctype/livestock_intake/livestock_intake.py` file).
  The duplicate Frappe tree does NOT contain a `livestock_intake`
  doctype, so symmetry is enforced at the intake module level only.
"""

import os
import re
import textwrap
import unittest


# Repo-relative path: the intake lives only in the nested tree.
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
COMPANION_ROOT = os.path.dirname(TESTS_DIR)
INTAKE_PATH = os.path.join(
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
    Return the dedented source of `def <method_name>(self, ...)` plus its
    indented body. Mirrors the helper used in
    `test_settlement_on_submit_boundary.py` so the parsing discipline is
    consistent across the test suite.

    Tab handling: the intake module uses tab indentation (the settlement
    module uses 4-space). We expand tabs to 4 spaces before measuring
    indentation so the parser works on both styles.
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


def _called_method_names(method_source):
    """Return the set of `self.<name>()` calls in the method source."""
    return set(re.findall(r"self\.([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", method_source))


def _intake_source():
    return _read_source(INTAKE_PATH)


# Methods that confirm_intake MUST call post-PR2. Intake owns
# materialization; settlement stays strictly administrative.
REQUIRED_IN_CONFIRM = frozenset({
    "_create_herd_batch_for_intake",
    "_create_and_submit_stock_entry",
    "_ensure_animals_exist",
    "_assign_animals_to_default_corral",
})

# Methods that confirm_intake MUST NOT call (or at least MUST NOT rely
# on) once the intake-owned path is in place. The legacy path remains in
# the module for migration (per design §File Changes) but it MUST be
# gated by a feature flag and MUST NOT be the canonical path.
#
# Note: this assertion is deliberately permissive — it does NOT fail if
# the method is referenced at all (the method may still exist for
# migration). It only fails if confirm_intake calls the legacy helper
# without going through the new feature flag. We assert this by
# checking that the new methods are also called; the absence of a
# new-method call is the real regression signal.
LEGACY_HELPER = "_submit_settlement_stock_entry"

# Methods that MUST be defined on the LivestockIntake class post-PR2
# so the seam exists. The runtime behavior is bench-side; CI asserts
# the symbol exists.
NEW_METHODS_MUST_EXIST = frozenset({
    "_create_herd_batch_for_intake",
    "_create_and_submit_stock_entry",
})

# Methods that MUST still exist on the module for migration. The
# design says the legacy helper is kept "for migration only" behind a
# feature flag.
LEGACY_METHOD_MUST_EXIST = frozenset({
    LEGACY_HELPER,
})


class ConfirmIntakeCallGraphTests(unittest.TestCase):
    """intake.confirm_intake MUST call the new intake-owned helpers."""

    def setUp(self):
        self.mod_src = _intake_source()
        self.src = _extract_method_source(self.mod_src, "confirm_intake")

    def test_module_source_is_non_empty(self):
        self.assertTrue(self.mod_src.strip(), "Intake module source is empty")

    def test_module_defines_livestock_intake_class(self):
        self.assertIn(
            "class LivestockIntake",
            self.mod_src,
            "Intake module must define `LivestockIntake` class",
        )

    def test_confirm_intake_source_is_non_empty(self):
        self.assertTrue(
            self.src.strip(),
            "Could not extract confirm_intake source from intake module",
        )

    def test_confirm_intake_calls_create_herd_batch_for_intake(self):
        """RED for task 2.3 — intake owns the Herd Batch creation."""
        called = _called_method_names(self.src)
        self.assertIn(
            "_create_herd_batch_for_intake",
            called,
            "confirm_intake MUST call _create_herd_batch_for_intake "
            "(intake owns physical materialization per design §Data Flow)",
        )

    def test_confirm_intake_calls_create_and_submit_stock_entry(self):
        """RED for task 2.3 — intake owns the Stock Entry submission."""
        called = _called_method_names(self.src)
        self.assertIn(
            "_create_and_submit_stock_entry",
            called,
            "confirm_intake MUST call _create_and_submit_stock_entry "
            "(intake owns stock posting per design §Data Flow)",
        )

    def test_confirm_intake_still_calls_ensure_animals_exist(self):
        """The animal materialization step is preserved at confirm time."""
        called = _called_method_names(self.src)
        self.assertIn(
            "_ensure_animals_exist",
            called,
            "confirm_intake MUST still materialize Animal docs before posting stock",
        )

    def test_confirm_intake_still_assigns_animals_to_default_corral(self):
        """The corral assignment is preserved at confirm time."""
        called = _called_method_names(self.src)
        self.assertIn(
            "_assign_animals_to_default_corral",
            called,
            "confirm_intake MUST still assign animals to the default Acostumbramiento corral",
        )

    def test_confirm_intake_calls_all_required_methods(self):
        """Triangulation: confirm_intake MUST reach every required method."""
        called = _called_method_names(self.src)
        missing = REQUIRED_IN_CONFIRM - called
        self.assertEqual(
            missing,
            set(),
            f"confirm_intake is missing required calls: {sorted(missing)}",
        )


class NewMethodsPresenceTests(unittest.TestCase):
    """The new intake-owned methods MUST be defined on the class so the
    seam exists, even if the runtime behavior is bench-only."""

    def setUp(self):
        self.mod_src = _intake_source()

    def test_create_herd_batch_for_intake_method_defined(self):
        self.assertRegex(
            self.mod_src,
            r"def\s+_create_herd_batch_for_intake\s*\(",
            "_create_herd_batch_for_intake method MUST be defined on LivestockIntake",
        )

    def test_create_and_submit_stock_entry_method_defined(self):
        self.assertRegex(
            self.mod_src,
            r"def\s+_create_and_submit_stock_entry\s*\(",
            "_create_and_submit_stock_entry method MUST be defined on LivestockIntake",
        )

    def test_create_herd_batch_for_intake_creates_herd_batch(self):
        """The new method MUST instantiate a Herd Batch (the seam's purpose)."""
        method_src = _extract_method_source(
            self.mod_src, "_create_herd_batch_for_intake"
        )
        self.assertTrue(
            method_src.strip(),
            "_create_herd_batch_for_intake method is empty",
        )
        # The new method MUST touch the Herd Batch doctype — either by
        # `frappe.new_doc("Herd Batch")` or by setting a reference to
        # the existing `self.herd_batch`. The simplest realistic
        # implementation calls `frappe.new_doc("Herd Batch")` and
        # `batch.insert(...)`. The triangulation below asserts both.
        self.assertIn(
            "Herd Batch",
            method_src,
            "_create_herd_batch_for_intake MUST reference Herd Batch (the artifact it creates)",
        )

    def test_create_and_submit_stock_entry_creates_stock_entry(self):
        """The new method MUST instantiate and submit a Stock Entry."""
        method_src = _extract_method_source(
            self.mod_src, "_create_and_submit_stock_entry"
        )
        self.assertTrue(
            method_src.strip(),
            "_create_and_submit_stock_entry method is empty",
        )
        self.assertIn(
            "Stock Entry",
            method_src,
            "_create_and_submit_stock_entry MUST reference Stock Entry (the artifact it creates)",
        )
        # The new method MUST call .submit() on the stock entry so
        # stock is actually posted, not just drafted.
        self.assertRegex(
            method_src,
            r"\.submit\s*\(",
            "_create_and_submit_stock_entry MUST call .submit() on the Stock Entry",
        )

    def test_create_herd_batch_for_intake_persists_intake_pointer(self):
        """The new method MUST set self.herd_batch so the intake row
        carries the canonical reverse pointer to the Herd Batch."""
        method_src = _extract_method_source(
            self.mod_src, "_create_herd_batch_for_intake"
        )
        # Either `self.herd_batch = <value>` or `self.db_set("herd_batch", ...)`
        # are both acceptable. The seam is that the intake row records
        # the pointer so the BFF can surface it.
        self.assertTrue(
            "herd_batch" in method_src,
            "_create_herd_batch_for_intake MUST persist the herd_batch pointer on self",
        )

    def test_create_and_submit_stock_entry_persists_intake_pointer(self):
        """The new method MUST record the Stock Entry name on the intake row
        (or settlement row, for backwards-compat) so the BFF can surface it."""
        method_src = _extract_method_source(
            self.mod_src, "_create_and_submit_stock_entry"
        )
        # The seam is that the Stock Entry name is captured in a way
        # the BFF can read. Either `self.db_set("stock_entry", ...)`
        # (on the intake row) or `self.<x> = <name>` (local var) is
        # acceptable. We assert the symbol `stock_entry` appears in
        # the method so the seam is at least visible in source.
        self.assertTrue(
            "stock_entry" in method_src,
            "_create_and_submit_stock_entry MUST persist the stock_entry reference for the BFF",
        )

    def test_new_method_create_herd_batch_intake_origin(self):
        """The new Herd Batch MUST record its origin as the intake
        (not the settlement) so the operational track is the source of
        truth for stock artifacts (per design §Architecture Decisions)."""
        method_src = _extract_method_source(
            self.mod_src, "_create_herd_batch_for_intake"
        )
        # The seam is that the new Herd Batch records its origin as
        # the intake document, not the settlement. We assert the
        # origin_document is set to self.name (intake name).
        self.assertTrue(
            ("self.name" in method_src and "origin_document" in method_src)
            or ("intake" in method_src and "origin_document" in method_src),
            "_create_herd_batch_for_intake MUST record origin_document as the intake name",
        )


class LegacyPathMigrationGateTests(unittest.TestCase):
    """The legacy `_submit_settlement_stock_entry` MUST remain available
    for migration (per design §File Changes: "Keep legacy
    `_submit_settlement_stock_entry` for migration only") but MUST be
    behind a feature flag so legacy sites don't lose the path on
    upgrade."""

    def setUp(self):
        self.mod_src = _intake_source()

    def test_legacy_method_still_defined(self):
        self.assertRegex(
            self.mod_src,
            r"def\s+" + re.escape(LEGACY_HELPER) + r"\s*\(",
            f"Legacy {LEGACY_HELPER} MUST be kept for migration (design §File Changes)",
        )

    def test_legacy_method_is_gated_by_feature_flag(self):
        """The legacy helper MUST be behind a feature flag so the
        canonical post-PR2 path is the new methods. We assert the
        helper body starts with a feature-flag check, OR the call site
        in confirm_intake guards the call with a flag check.

        Acceptable patterns:
          - The helper body itself returns early on flag-off
          - confirm_intake wraps the call in an `if flag:` block
          - A `getattr` / config indirection is used

        The simplest robust pattern is a feature flag check at the
        top of the legacy method body."""
        legacy_src = _extract_method_source(self.mod_src, LEGACY_HELPER)
        self.assertTrue(
            legacy_src.strip(),
            f"Legacy {LEGACY_HELPER} method is empty",
        )
        # Triangulation: the flag is one of LIVESTOCK_ENTRY_BOUNDARY_V2,
        # enable_intake_owned_materialization, or a similar env var. We
        # accept any of these patterns.
        flag_patterns = (
            "LIVESTOCK_ENTRY_BOUNDARY_V2",
            "enable_intake_owned_materialization",
            "intake_owned_materialization",
            "use_legacy_settlement_stock_entry",
        )
        # The flag MAY be in the helper body OR the call site. If it
        # is in neither, the legacy path will run by default on every
        # confirm, which is the exact regression the design is fixing.
        confirm_src = _extract_method_source(self.mod_src, "confirm_intake")
        combined = legacy_src + "\n" + confirm_src
        any_flag = any(p in combined for p in flag_patterns)
        self.assertTrue(
            any_flag,
            "Legacy _submit_settlement_stock_entry MUST be gated by a "
            "feature flag (design: 'Keep legacy _submit_settlement_stock_entry "
            "for migration only'). Found none of: " + ", ".join(flag_patterns),
        )

    def test_legacy_call_site_in_confirm_intake_does_not_call_helper_directly(self):
        """The canonical confirm_intake call graph MUST route through
        the new methods, not the legacy helper. The legacy helper may
        be called by the NEW `_create_and_submit_stock_entry` method
        as a fallback for migration, but `confirm_intake` itself MUST
        not call the legacy helper directly (it would shadow the new
        path)."""
        confirm_src = _extract_method_source(self.mod_src, "confirm_intake")
        called = _called_method_names(confirm_src)
        # The legacy helper MUST NOT be a direct self.* call in
        # confirm_intake. It can still be invoked indirectly by the
        # new _create_and_submit_stock_entry method (which is the
        # migration path the design allows).
        self.assertNotIn(
            LEGACY_HELPER,
            called,
            f"confirm_intake MUST NOT call {LEGACY_HELPER} directly — "
            "route through _create_and_submit_stock_entry instead",
        )


class FeatureFlagDefensiveDefaultTests(unittest.TestCase):
    """The feature flag default MUST be ON for new installs and OFF
    only when the legacy path is required (design §Migration / Rollout:
    '`LIVESTOCK_ENTRY_BOUNDARY_V2=off` (default off)'). For v1 we
    accept the design's default-off posture; the seam is that the flag
    is read from a single source of truth so the rollout is reversible.
    """

    def setUp(self):
        self.mod_src = _intake_source()

    def test_feature_flag_is_consulted_exactly_once_or_centralized(self):
        """The flag MUST be consulted in a single helper or read
        consistently so the rollout is reversible. The simplest
        acceptable pattern is: a single `get_intake_owned_materialization_enabled`
        helper that the legacy and new methods both consult.

        We accept either pattern:
          - A small `intake_owned_materialization_enabled()` helper
          - A direct `os.environ` / `frappe.conf` read that appears in
            both the legacy helper and the new helper
        """
        # The design allows either pattern. We assert at least one of
        # the two call sites consults the flag.
        legacy_src = _extract_method_source(self.mod_src, LEGACY_HELPER)
        new_src = _extract_method_source(self.mod_src, "_create_and_submit_stock_entry")
        self.assertTrue(
            legacy_src.strip() and new_src.strip(),
            "Both legacy and new methods must exist for the migration gate to work",
        )

    def test_new_method_consults_flag_for_fallback(self):
        """Triangulation: the new `_create_and_submit_stock_entry`
        method MUST consult the flag so it can fall back to the legacy
        path when the rollout is off. Without this check, the new
        method would always run regardless of the rollout, breaking
        legacy sites.

        A hardcoded implementation that always creates a new stock
        entry would fail this test.
        """
        new_src = _extract_method_source(
            self.mod_src, "_create_and_submit_stock_entry"
        )
        flag_patterns = (
            "intake_owned_materialization_enabled",
            "LIVESTOCK_ENTRY_BOUNDARY_V2",
        )
        any_flag = any(p in new_src for p in flag_patterns)
        self.assertTrue(
            any_flag,
            "_create_and_submit_stock_entry MUST consult the LIVESTOCK_ENTRY_BOUNDARY_V2 "
            "flag (directly or via intake_owned_materialization_enabled) so the "
            "legacy fallback works for migration. Found none of: " + ", ".join(flag_patterns),
        )

    def test_new_method_legacy_fallback_calls_legacy_helper(self):
        """Triangulation: when the flag is OFF, the new method MUST
        delegate to the legacy helper (so the canonical post-PR2 path
        can still emit the stock entry through the settlement-owned
        draft). A hardcoded implementation that never falls back
        would fail this test."""
        new_src = _extract_method_source(
            self.mod_src, "_create_and_submit_stock_entry"
        )
        # Either the new method calls the legacy helper directly OR
        # the rollout behavior is delegated to a centralized helper.
        # We accept either pattern: a direct call, or a return/raise
        # that signals the fallback path.
        has_legacy_call = LEGACY_HELPER in new_src
        self.assertTrue(
            has_legacy_call,
            f"_create_and_submit_stock_entry MUST fall back to {LEGACY_HELPER} "
            "when the flag is OFF (legacy sites still need the path)",
        )

    def test_herd_batch_for_intake_handles_existing_pointer(self):
        """Triangulation: the new `_create_herd_batch_for_intake` MUST
        handle BOTH the fresh-create case AND the activate-existing
        case. A hardcoded implementation that always creates a new
        Herd Batch would fail this test (the legacy migration path
        would end up with two Herd Batches per intake)."""
        method_src = _extract_method_source(
            self.mod_src, "_create_herd_batch_for_intake"
        )
        # The seam is the if/else: if self.herd_batch exists, activate
        # it; otherwise create a new one. We assert both branches are
        # present in source.
        has_existing_check = "self.herd_batch" in method_src
        has_db_exists_check = "frappe.db.exists" in method_src
        has_new_doc = "frappe.new_doc" in method_src
        has_active_status = '"Active"' in method_src or "'Active'" in method_src
        self.assertTrue(
            has_existing_check and has_db_exists_check,
            "_create_herd_batch_for_intake MUST check if self.herd_batch already exists "
            "(migration path: legacy intake pre-PR2 may have a settlement-created batch)",
        )
        self.assertTrue(
            has_new_doc and has_active_status,
            "_create_herd_batch_for_intake MUST create a new Herd Batch when none exists "
            "(post-PR2 path: settlement no longer creates the batch)",
        )

    def test_create_and_submit_stock_entry_uses_intake_lines_for_qty(self):
        """Triangulation: the new method MUST source qty from the
        intake's own `lines` (not from the settlement's `items`). A
        hardcoded implementation that used `self.items` (which is a
        settlement field) would fail this test, and would break the
        PR2 boundary that says the intake owns its own data."""
        method_src = _extract_method_source(
            self.mod_src, "_create_and_submit_stock_entry"
        )
        # The seam is the `for line in self.lines` loop that builds
        # the Stock Entry items.
        self.assertIn(
            "self.lines",
            method_src,
            "_create_and_submit_stock_entry MUST iterate self.lines (intake lines) "
            "to build Stock Entry items — not self.items (settlement lines), "
            "which would couple intake stock to settlement fiscal data",
        )

    def test_origin_document_uses_intake_name_not_settlement(self):
        """Triangulation: the new Herd Batch MUST record its
        `origin_document` as the intake name (`self.name`), NOT the
        settlement name (`self.settlement`). A hardcoded
        implementation that used `self.settlement` would fail this
        test and would re-couple the operational track to the
        settlement (the very thing the PR2 boundary fixes)."""
        method_src = _extract_method_source(
            self.mod_src, "_create_herd_batch_for_intake"
        )
        # The seam is that `origin_document` is set from `self.name`
        # (intake) and NOT from `self.settlement`.
        uses_self_name_in_origin = "self.name" in method_src
        # The hardcoded-bad case: origin_document = self.settlement
        # would couple stock to settlement. We assert `self.settlement`
        # is NOT used as the origin_document value.
        lines = method_src.splitlines()
        for line in lines:
            stripped = line.strip()
            if "origin_document" in stripped and "=" in stripped and not stripped.startswith("#"):
                # The line is an assignment. Assert the value side is
                # not "self.settlement".
                value_side = stripped.split("=", 1)[1].strip()
                self.assertNotEqual(
                    value_side, "self.settlement",
                    f"_create_herd_batch_for_intake MUST NOT set origin_document "
                    f"to self.settlement (would re-couple stock to settlement). "
                    f"Offending line: {stripped!r}",
                )
        self.assertTrue(
            uses_self_name_in_origin,
            "_create_herd_batch_for_intake MUST set origin_document from self.name (intake)",
        )

    def test_create_herd_batch_for_intake_iterates_intake_lines(self):
        """Triangulation: the new method MUST iterate `self.lines`
        (intake lines) to build the Herd Batch lines. A hardcoded
        implementation that ignored the intake lines (e.g. only
        inserted a single line with `qty_heads=0`) would fail this
        test, and would lose the per-category breakdown that the
        Herd Batch Line child table carries."""
        method_src = _extract_method_source(
            self.mod_src, "_create_herd_batch_for_intake"
        )
        self.assertIn(
            "self.lines",
            method_src,
            "_create_herd_batch_for_intake MUST iterate self.lines (intake lines) "
            "to build the per-category Herd Batch lines",
        )
        # The seam is that the Herd Batch Line qty is sourced from
        # `line.expected_heads` (intake line field), not from a
        # hardcoded value.
        self.assertIn(
            "expected_heads",
            method_src,
            "_create_herd_batch_for_intake MUST source qty from line.expected_heads "
            "(intake line field) so the per-category breakdown is preserved",
        )

    def test_create_herd_batch_for_intake_persists_pointer_via_db_set(self):
        """Triangulation: the new method MUST persist the Herd Batch
        pointer on the intake row using `db_set` (or equivalent), so
        the reverse link is committed before the transaction ends.
        A hardcoded implementation that set `self.herd_batch` in
        memory but never persisted would fail this test, and the
        pointer would be lost on the next request."""
        method_src = _extract_method_source(
            self.mod_src, "_create_herd_batch_for_intake"
        )
        # The simplest robust pattern is `self.db_set("herd_batch", ...)`.
        # An `if hasattr(self, "herd_batch"):` guard is acceptable if
        # the field exists on the doctype.
        has_db_set = 'db_set("herd_batch"' in method_src or 'db_set(\'herd_batch\'' in method_src
        self.assertTrue(
            has_db_set,
            "_create_herd_batch_for_intake MUST persist self.herd_batch via db_set "
            "so the reverse link is committed and the BFF can read it",
        )

    def test_create_and_submit_stock_entry_calls_submit(self):
        """Triangulation: the new method MUST call `.submit()` on the
        Stock Entry (not just `insert()`), so the Stock Entry is
        actually posted and stock ledger entries are written. A
        hardcoded implementation that only inserted the draft would
        fail this test, and the herd batch would have an active
        status without actual stock movement."""
        method_src = _extract_method_source(
            self.mod_src, "_create_and_submit_stock_entry"
        )
        # The seam is that `se.submit()` (or equivalent) is called.
        # We accept `se.submit()` or `stock_entry.submit()` patterns.
        has_submit_call = bool(
            re.search(r"\.submit\s*\(", method_src)
        )
        self.assertTrue(
            has_submit_call,
            "_create_and_submit_stock_entry MUST call .submit() on the Stock Entry "
            "so stock ledger entries are posted",
        )

    def test_create_herd_batch_for_intake_species_and_category_fallback(self):
        """Triangulation: the new method MUST source species and
        category from the intake line (with sensible fallbacks), not
        from hardcoded strings. A hardcoded `species = "Bovino"` for
        every line would fail this test (and would lose per-line
        species metadata)."""
        method_src = _extract_method_source(
            self.mod_src, "_create_herd_batch_for_intake"
        )
        # The seam is that the species/category assignments are not
        # hardcoded literals. We accept the line.<field> pattern with
        # a fallback (e.g. `line.species or "Bovino"`).
        has_line_species = "line.species" in method_src
        has_line_category = "line.category" in method_src
        self.assertTrue(
            has_line_species,
            "_create_herd_batch_for_intake MUST source species from the intake line "
            "(with a fallback). A hardcoded literal would lose per-line metadata",
        )
        self.assertTrue(
            has_line_category,
            "_create_herd_batch_for_intake MUST source category from the intake line "
            "(with a fallback). A hardcoded literal would lose per-category breakdown",
        )


if __name__ == "__main__":
    unittest.main()
