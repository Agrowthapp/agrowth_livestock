"""
Tests for BUG 3: `list_grouped_labores` (and `list_labores`,
`get_labor_group_detail`) fails with `Unknown column 'ear_tag_id' in
'SELECT'` (and likely other missing fields like `line_index`,
`event_group_id`, `scope_type`, etc.).

Root cause: `EVENT_FIELDS` in `agrowth_livestock/api/labors.py` lists
fields that are not declared on the `Animal Event` doctype and are not
present as `Custom Field` rows. `frappe.get_all("Animal Event",
fields=EVENT_FIELDS, ...)` builds a `SELECT *` that includes the
missing column, and MySQL returns `1054 Unknown column 'X' in 'SELECT'`.

The fix MUST be defensive field selection: filter `EVENT_FIELDS` to
only the fields that exist on the doctype (standard fields + meta
fields + custom fields). The `_map_history_row` function already uses
`.get()` for every field, so missing fields return `None` safely.

For `ear_tag_id` specifically, the BFF DTO `LaborHistoryRow` expects
the field to be populated. The fix MUST also resolve `ear_tag_id` from
the linked `Animal` row when the column is missing on the event, so
the BFF still gets the ear tag id without requiring a Frappe migration
or a BFF retry pattern.

Why source-parse? Same discipline as the other BUG tests — the BFF
CI does not run a Frappe bench, and `api/labors.py` imports `frappe`
at module load. The runtime behavior of the defensive field filter is
exercised by bench smoke tests; CI keeps the regression net tight by
asserting the seam at source level.

Triangulation:

  * `EVENT_FIELDS` MUST be filtered (via a helper or inline) to only
    fields that exist on the Animal Event doctype, so the SQL query
    never requests a missing column.
  * Each call site of `EVENT_FIELDS` (list_labores, list_grouped_labores,
    get_labor_group_detail) MUST receive a defensive field list. A
    hardcoded `EVENT_FIELDS` constant that includes missing fields
    would still 500.
  * For the `ear_tag_id` semantic: the API MUST resolve it from the
    linked `Animal` row when the column is missing on the event. The
    simplest pattern is a follow-up `frappe.get_all("Animal", ...)`
    that fetches the `ear_tag_id` for the unique set of `animal` ids
    referenced by the events, and the mapping function joins it back.
"""

import os
import re
import unittest


TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
COMPANION_ROOT = os.path.dirname(TESTS_DIR)
LABORS_API_PATH = os.path.join(COMPANION_ROOT, "api", "labors.py")


def _read_source(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class EventFieldsDefensiveSelectionTests(unittest.TestCase):
    """The `EVENT_FIELDS` constant MUST be filtered to only fields that
    exist on the Animal Event doctype, so the SQL query never requests
    a missing column."""

    def setUp(self):
        self.src = _read_source(LABORS_API_PATH)

    def test_event_fields_constant_defined(self):
        self.assertTrue(
            bool(re.search(r"^EVENT_FIELDS\s*=\s*\[", self.src, re.MULTILINE)),
            "agrowth_livestock/api/labors.py MUST define the EVENT_FIELDS constant",
        )

    def test_event_fields_is_filtered_to_existing_fields(self):
        """The seam: there MUST be a helper that filters EVENT_FIELDS to
        only fields that exist on the Animal Event doctype. The simplest
        robust pattern is a small helper (e.g. `_existing_event_fields`)
        that calls `frappe.get_meta('Animal Event').get_field(...)`."""
        # Accept any of these patterns:
        #   - a helper named `_existing_event_fields` or similar
        #   - a direct call to `frappe.get_meta("Animal Event")` near
        #     the call sites
        helper_patterns = (
            "frappe.get_meta(\"Animal Event\")",
            'frappe.get_meta("Animal Event")',
            "frappe.get_meta('Animal Event')",
            "_existing_event_fields(",
            "_event_fields(",
            "existing_event_fields(",
            "_filter_event_fields(",
        )
        any_helper = any(p in self.src for p in helper_patterns)
        self.assertTrue(
            any_helper,
            "agrowth_livestock/api/labors.py MUST defensively filter "
            "EVENT_FIELDS to only fields that exist on the Animal Event "
            "doctype (via frappe.get_meta or a helper). Without this, "
            "frappe.get_all('Animal Event', fields=EVENT_FIELDS, ...) "
            "raises MySQL 1054 'Unknown column' when EVENT_FIELDS lists "
            "fields that are not in the schema (e.g. ear_tag_id). "
            "Found none of: " + ", ".join(helper_patterns),
        )

    def _find_function(self, name):
        m = re.search(rf"def\s+{name}\b[\s\S]*?(?=^def\s+|\Z)", self.src, re.MULTILINE)
        return m.group(0) if m else ""

    def test_list_labores_uses_defensive_fields(self):
        body = self._find_function("list_labores")
        self.assertTrue(body, "list_labores must be defined")
        defensive_patterns = (
            "frappe.get_meta(\"Animal Event\")",
            "frappe.get_meta('Animal Event')",
            "_existing_event_fields(",
            "_event_fields(",
            "existing_event_fields(",
        )
        has_defensive = any(p in body for p in defensive_patterns)
        self.assertTrue(
            has_defensive,
            "list_labores MUST defensively filter EVENT_FIELDS to only "
            "fields that exist on the Animal Event doctype, so the SQL "
            "query never requests a missing column. Found none of: "
            + ", ".join(defensive_patterns),
        )

    def test_list_grouped_labores_uses_defensive_fields(self):
        body = self._find_function("list_grouped_labores")
        self.assertTrue(body, "list_grouped_labores must be defined")
        defensive_patterns = (
            "frappe.get_meta(\"Animal Event\")",
            "frappe.get_meta('Animal Event')",
            "_existing_event_fields(",
            "_event_fields(",
            "existing_event_fields(",
        )
        has_defensive = any(p in body for p in defensive_patterns)
        self.assertTrue(
            has_defensive,
            "list_grouped_labores MUST defensively filter EVENT_FIELDS to "
            "only fields that exist on the Animal Event doctype. Found "
            "none of: " + ", ".join(defensive_patterns),
        )

    def test_get_labor_group_detail_uses_defensive_fields(self):
        body = self._find_function("get_labor_group_detail")
        self.assertTrue(body, "get_labor_group_detail must be defined")
        defensive_patterns = (
            "frappe.get_meta(\"Animal Event\")",
            "frappe.get_meta('Animal Event')",
            "_existing_event_fields(",
            "_event_fields(",
            "existing_event_fields(",
        )
        has_defensive = any(p in body for p in defensive_patterns)
        self.assertTrue(
            has_defensive,
            "get_labor_group_detail MUST defensively filter EVENT_FIELDS to "
            "only fields that exist on the Animal Event doctype. Found "
            "none of: " + ", ".join(defensive_patterns),
        )


class EarTagIdResolutionTests(unittest.TestCase):
    """The `_map_history_row` (or the wrapper) MUST resolve `ear_tag_id`
    from the linked `Animal` row when the column is missing on the
    event, so the BFF still gets the ear tag id without requiring a
    Frappe migration or a BFF retry pattern.

    The simplest pattern is a follow-up `frappe.get_all("Animal", ...)`
    that fetches `ear_tag_id` for the unique set of `animal` ids
    referenced by the events. The seam is the lookup: the BFF DTO
    `LaborHistoryRow.earTagId` MUST be populated from either the
    event row directly OR the animal row.
    """

    def setUp(self):
        self.src = _read_source(LABORS_API_PATH)

    def test_ear_tag_id_resolution_uses_animal_link(self):
        """The API MUST resolve `ear_tag_id` from the linked `Animal`
        row at least once (in list_labores, list_grouped_labores, or
        get_labor_group_detail). A pure-defensive-filter fix that just
        drops `ear_tag_id` would still 500 in the DTO mapping for
        any code path that tries to read it."""
        patterns = (
            "frappe.get_all(\"Animal\"",
            'frappe.get_all("Animal"',
            "frappe.get_all('Animal'",
            "_resolve_animal_ear_tags",
            "_fetch_animal_ear_tags",
            "ear_tag_id_by_animal",
        )
        any_pattern = any(p in self.src for p in patterns)
        self.assertTrue(
            any_pattern,
            "agrowth_livestock/api/labors.py MUST resolve `ear_tag_id` "
            "from the linked `Animal` row when the column is missing on "
            "the event, so the BFF DTO LaborHistoryRow.earTagId is still "
            "populated. Found none of: " + ", ".join(patterns),
        )


if __name__ == "__main__":
    unittest.main()
