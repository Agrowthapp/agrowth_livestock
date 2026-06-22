"""
Tests for BUG 2: `agrowth_livestock.api.stock` is `No module named`.

The BFF calls three Frappe whitelisted methods on the stock module:

  * `agrowth_livestock.api.stock.get_summary`
  * `agrowth_livestock.api.stock.get_availability`
  * `agrowth_livestock.api.stock.get_ledger`

The file `agrowth_livestock/api/stock.py` does NOT exist, so every call
returns 417 with `No module named 'agrowth_livestock.api.stock'`. The
BFF has a defensive fallback (`isMethodNotFoundError`) that returns an
empty payload, but the user is still seeing the 417 in the network
panel and the page renders empty data instead of the real stock.

The fix is to create the missing module with the three whitelisted
methods, implementing the data the BFF DTOs expect.

Why source-parse? Same discipline as
`test_intake_confirm_warehouse_fallback.py` — the BFF CI does not run
a Frappe bench, and `api/stock.py` imports `frappe` at module load.
The runtime behavior of the methods is exercised by bench smoke tests;
CI keeps the regression net tight by asserting the seam at source
level.

Triangulation:

  * The module MUST exist at `agrowth_livestock/api/stock.py`.
  * The module MUST define the three methods with the exact names the
    BFF calls (the BFF's method names are contractually fixed; renaming
    would break the BFF without an update).
  * Each method MUST be decorated with `@frappe.whitelist()` so the
    HTTP method-not-found error stops.
  * Each method MUST accept at minimum `company_id` (the BFF sends
    `company_id` in every payload) and any other parameter the BFF
    sends (category, sex, page, limit, tropa, ingreso, from_date,
    to_date).
  * The response shape MUST match the BFF DTOs in
    `src/features/ganaderia/services/stock.models.ts`:
      - get_summary returns `{ total, conCaravana, sinCaravana, buckets }`
      - get_availability returns `{ available, bucket: { total, ... } }`
      - get_ledger returns `{ entries: [...], page, limit, total? }`
"""

import ast
import os
import re
import unittest


TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
COMPANION_ROOT = os.path.dirname(TESTS_DIR)
STOCK_API_PATH = os.path.join(COMPANION_ROOT, "api", "stock.py")


REQUIRED_METHODS = {
    "get_summary",
    "get_availability",
    "get_ledger",
}


class StockApiModulePresenceTests(unittest.TestCase):
    """The stock API module MUST exist at the canonical path."""

    def test_stock_api_module_file_exists(self):
        self.assertTrue(
            os.path.isfile(STOCK_API_PATH),
            f"agrowth_livestock/api/stock.py MUST exist at {STOCK_API_PATH} "
            "so the BFF's three whitelisted methods resolve. "
            "Without this file, every call returns 417 No module named.",
        )


class StockApiMethodSignatureTests(unittest.TestCase):
    """Each required method MUST be defined, decorated with @frappe.whitelist(),
    and accept the parameters the BFF sends. We parse the AST so the
    assertions are independent of tab/space indentation or comment style."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(STOCK_API_PATH):
            raise unittest.SkipTest(
                f"stock.py does not exist yet at {STOCK_API_PATH} — "
                "skipping signature tests"
            )
        with open(STOCK_API_PATH, encoding="utf-8") as fh:
            cls.tree = ast.parse(fh.read(), filename=STOCK_API_PATH)

    def _find_function(self, name):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        return None

    def _is_whitelisted(self, fn):
        return any(
            (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "whitelist"
            )
            or (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Name)
                and dec.func.id == "whitelist"
            )
            for dec in fn.decorator_list
        )

    def test_get_summary_defined_and_whitelisted(self):
        fn = self._find_function("get_summary")
        self.assertIsNotNone(
            fn,
            "agrowth_livestock/api/stock.py MUST define a top-level "
            "function `get_summary` (the BFF calls this name verbatim)",
        )
        self.assertTrue(
            self._is_whitelisted(fn),
            "stock.py:get_summary MUST be decorated with @frappe.whitelist() "
            "so the BFF's HTTP method-not-found error stops. A bare def "
            "would still return 417.",
        )

    def test_get_availability_defined_and_whitelisted(self):
        fn = self._find_function("get_availability")
        self.assertIsNotNone(
            fn,
            "agrowth_livestock/api/stock.py MUST define a top-level "
            "function `get_availability` (the BFF calls this name verbatim)",
        )
        self.assertTrue(
            self._is_whitelisted(fn),
            "stock.py:get_availability MUST be decorated with @frappe.whitelist()",
        )

    def test_get_ledger_defined_and_whitelisted(self):
        fn = self._find_function("get_ledger")
        self.assertIsNotNone(
            fn,
            "agrowth_livestock/api/stock.py MUST define a top-level "
            "function `get_ledger` (the BFF calls this name verbatim)",
        )
        self.assertTrue(
            self._is_whitelisted(fn),
            "stock.py:get_ledger MUST be decorated with @frappe.whitelist()",
        )

    def test_get_summary_accepts_company_id(self):
        fn = self._find_function("get_summary")
        self.assertIsNotNone(fn, "get_summary must be defined")
        arg_names = {a.arg for a in fn.args.args}
        self.assertIn(
            "company_id",
            arg_names,
            "stock.py:get_summary MUST accept `company_id` (the BFF sends "
            "company_id in every payload). A method without company_id "
            "would still 417 because the BFF calls it with that kwarg.",
        )

    def test_get_availability_accepts_company_id(self):
        fn = self._find_function("get_availability")
        self.assertIsNotNone(fn, "get_availability must be defined")
        arg_names = {a.arg for a in fn.args.args}
        self.assertIn(
            "company_id",
            arg_names,
            "stock.py:get_availability MUST accept `company_id`",
        )

    def test_get_ledger_accepts_company_id(self):
        fn = self._find_function("get_ledger")
        self.assertIsNotNone(fn, "get_ledger must be defined")
        arg_names = {a.arg for a in fn.args.args}
        self.assertIn(
            "company_id",
            arg_names,
            "stock.py:get_ledger MUST accept `company_id`",
        )


class StockApiResponseShapeTests(unittest.TestCase):
    """Each method MUST return a payload that matches the BFF DTOs in
    `src/features/ganaderia/services/stock.models.ts`. We assert by
    reading the source — the response-building logic must reference
    the documented fields (total, conCaravana, sinCaravana, buckets,
    available, bucket, entries)."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(STOCK_API_PATH):
            raise unittest.SkipTest("stock.py does not exist yet")
        with open(STOCK_API_PATH, encoding="utf-8") as fh:
            cls.source = fh.read()

    def _method_body(self, name):
        # Match the method body up to the next top-level def or end of file.
        m = re.search(rf"def\s+{name}\b[\s\S]*?(?=^def\s+|\Z)", self.source, re.MULTILINE)
        return m.group(0) if m else ""

    def test_get_summary_returns_total_and_buckets_fields(self):
        body = self._method_body("get_summary")
        for key in ("total", "conCaravana", "sinCaravana", "buckets"):
            self.assertIn(
                key,
                body,
                f"stock.py:get_summary MUST return a payload containing "
                f"`{key}` (the BFF DTO StockSummaryResponse expects it). "
                "Without this field, the BFF would still receive an empty "
                "response and the page would render zero stock.",
            )

    def test_get_availability_returns_available_and_bucket(self):
        body = self._method_body("get_availability")
        for key in ("available", "bucket"):
            self.assertIn(
                key,
                body,
                f"stock.py:get_availability MUST return a payload containing "
                f"`{key}` (the BFF DTO StockAvailabilityResponse expects it)",
            )

    def test_get_ledger_returns_entries_page_limit(self):
        body = self._method_body("get_ledger")
        for key in ("entries", "page", "limit"):
            self.assertIn(
                key,
                body,
                f"stock.py:get_ledger MUST return a payload containing "
                f"`{key}` (the BFF ledger response expects it)",
            )


if __name__ == "__main__":
    unittest.main()
