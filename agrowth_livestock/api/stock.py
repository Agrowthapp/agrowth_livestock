"""
Stock API for the Ganadería BFF.

Provides the three whitelisted methods the BFF calls
(`get_summary`, `get_availability`, `get_ledger`). The DTOs the BFF
expects are documented in
`src/features/ganaderia/services/stock.models.ts` (StockSummaryResponse,
StockAvailabilityResponse, StockLedgerEntry).

Why this module exists (BUG 2 fix): the BFF calls these methods by
name. The previous version of the app did not ship
`agrowth_livestock/api/stock.py`, so every call returned 417 with
`No module named 'agrowth_livestock.api.stock'`. The BFF has a
defensive fallback that returns an empty payload, but the user was
still seeing the 417 in the network panel and the stock page rendered
zero data.

Stock model: an "animal on hand" is an `Animal` doc with `disabled = 0`
and a `company` matching the requested tenant. The `warehouse` field
on `Animal` carries the current physical location (a corral
`Warehouse` with `is_corral = 1`). `conCaravana` is a real ear-tagged
animal (no `SIN-CARAVANA-` placeholder prefix); `sinCaravana` is a
placeholder. Bucket aggregation is `(current_category, sex, tropa,
ingreso)`.

Resolution order for the lookup is defensive: the field set is built
from `frappe.get_meta` so pre-deploy and post-deploy sites both work
without 417. The Animal doc fields referenced by these methods are
all in the base schema, so no Custom Field is required.
"""

import frappe
from frappe.utils import cint


PLACEHOLDER_PREFIX = "SIN-CARAVANA-"


ANIMAL_FIELDS = [
    "name",
    "ear_tag_id",
    "current_category",
    "sex",
    "current_herd_batch",
    "warehouse",
    "disabled",
    "company",
]


def _existing_fields(doctype, requested_fields):
    meta = frappe.get_meta(doctype)
    return [field for field in requested_fields if meta.get_field(field)]


def _has_placeholder(ear_tag_id):
    return str(ear_tag_id or "").startswith(PLACEHOLDER_PREFIX)


def _map_bucket(row):
    return {
        "total": cint(row.get("total") or 0),
        "conCaravana": cint(row.get("conCaravana") or 0),
        "sinCaravana": cint(row.get("sinCaravana") or 0),
        "category": row.get("category") or None,
        "sex": row.get("sex") or None,
        "tropa": row.get("tropa") or None,
        "ingreso": row.get("ingreso") or None,
    }


def _is_placeholder_animal(animal):
    return _has_placeholder(animal.get("ear_tag_id"))


def _herd_batch_ids_for_intake(intake_id):
    """Return the set of Herd Batch names whose `origin_document` is the
    given intake. Used so the `ingreso` filter on stock aggregates
    counts animals that were materialized from that intake."""
    if not intake_id:
        return []
    rows = frappe.get_all(
        "Herd Batch",
        filters=[
            ["origin_type", "=", "Livestock Intake"],
            ["origin_document", "=", intake_id],
        ],
        fields=["name"],
        limit_page_length=200,
    )
    return [str(r.get("name") or "") for r in rows if r.get("name")]


def _animal_filters(company_id, category=None, sex=None, tropa=None, ingreso=None):
    """Build the Animal filter list for stock queries.

    `company` is mandatory (tenant isolation). All other filters are
    optional and applied with `=`. `disabled = 0` is enforced so sold /
    dead animals do not show up as on-hand stock.
    """
    filters = [
        ["company", "=", company_id],
        ["disabled", "=", 0],
    ]
    if category:
        filters.append(["current_category", "=", category])
    if sex:
        filters.append(["sex", "=", sex])
    if tropa:
        filters.append(["current_herd_batch", "=", tropa])
    if ingreso:
        # `ingreso` in the DTO is the intake id; the Animal row links
        # to it via `origin_document` when `origin_type = "Livestock Intake"`,
        # or via `current_herd_batch -> origin_document` when the animal
        # was materialized from a confirmed intake. We resolve via the
        # `current_herd_batch.origin_document` join so the filter stays
        # a single SQL statement.
        filters.append(["current_herd_batch", "in", _herd_batch_ids_for_intake(ingreso)])
    return filters


def _fetch_animals(company_id, category=None, sex=None, tropa=None, ingreso=None):
    """Fetch the on-hand Animal rows for the given filters. Defensive
    field selection: only request fields that exist on the doctype so
    pre/post-migration sites both work."""
    selected = _existing_fields("Animal", ANIMAL_FIELDS)
    filters = _animal_filters(company_id, category=category, sex=sex, tropa=tropa, ingreso=ingreso)
    # When the `ingreso` filter is set but resolves to an empty batch
    # list, return early so we don't run a query with a useless `IN ()`
    # filter (some SQL drivers reject it).
    if ingreso and not filters[-1][2]:
        return []
    return frappe.get_all(
        "Animal",
        filters=filters,
        fields=selected,
        limit_page_length=0,  # no pagination for the count path
    )


def _summarize_animals(animals):
    """Compute total / conCaravana / sinCaravana counts from a list of
    animal rows. The DTO uses `conCaravana` for real EIDs and
    `sinCaravana` for placeholder EIDs (no ear tag yet)."""
    total = 0
    con_caravana = 0
    sin_caravana = 0
    for animal in animals:
        if cint(animal.get("disabled") or 0):
            continue
        total += 1
        if _is_placeholder_animal(animal):
            sin_caravana += 1
        else:
            con_caravana += 1
    return total, con_caravana, sin_caravana


def _bucket_key(animal):
    return (
        str(animal.get("current_category") or ""),
        str(animal.get("sex") or ""),
        str(animal.get("current_herd_batch") or ""),
    )


@frappe.whitelist()
def get_summary(company_id, category=None, sex=None, page=1, limit=20):
    """Return the on-hand stock summary for the given tenant.

    Response shape (matches `StockSummaryResponse` in
    `src/features/ganaderia/services/stock.models.ts`):
      {
        total: int,
        conCaravana: int,
        sinCaravana: int,
        buckets: [StockBucketSummary]
      }

    Buckets are aggregated by (category, sex, tropa) so the BFF can
    render a per-category breakdown without a second call.
    """
    page = max(cint(page), 1)
    limit = min(max(cint(limit), 1), 200)
    animals = _fetch_animals(company_id, category=category, sex=sex)

    total, con_caravana, sin_caravana = _summarize_animals(animals)

    bucket_map = {}
    for animal in animals:
        key = _bucket_key(animal)
        bucket = bucket_map.setdefault(
            key,
            {
                "total": 0,
                "conCaravana": 0,
                "sinCaravana": 0,
                "category": animal.get("current_category") or None,
                "sex": animal.get("sex") or None,
                "tropa": animal.get("current_herd_batch") or None,
            },
        )
        bucket["total"] += 1
        if _is_placeholder_animal(animal):
            bucket["sinCaravana"] += 1
        else:
            bucket["conCaravana"] += 1

    # Aggregate Livestock Stock Ledger Entry rows for opening/adjustment movements
    ledger_entries = frappe.db.sql(
        """
        SELECT category, sex, heads_qty
        FROM `tabLivestockStockLedgerEntry`
        WHERE company = %s AND movement_type IN ('opening', 'opening_adjustment')
        """,
        (company_id,),
        as_dict=True,
    )

    for entry in ledger_entries:
        key = (
            str(entry.get("category") or ""),
            str(entry.get("sex") or ""),
            "",  # No herd_batch for opening entries
        )
        bucket = bucket_map.setdefault(
            key,
            {
                "total": 0,
                "conCaravana": 0,
                "sinCaravana": 0,
                "category": entry.get("category") or None,
                "sex": entry.get("sex") or None,
                "tropa": None,
            },
        )
        qty = cint(entry.get("heads_qty") or 0)
        bucket["total"] += qty
        bucket["sinCaravana"] += qty  # Opening entries have no ear tags
        total += qty
        sin_caravana += qty

    buckets = [_map_bucket(b) for b in bucket_map.values()]

    return {
        "total": total,
        "conCaravana": con_caravana,
        "sinCaravana": sin_caravana,
        "buckets": buckets,
    }


@frappe.whitelist()
def get_availability(company_id, category=None, sex=None, tropa=None, ingreso=None):
    """Return whether the requested stock is available, and the bucket
    that would be used.

    Response shape (matches `StockAvailabilityResponse` in
    `src/features/ganaderia/services/stock.models.ts`):
      { available: bool, bucket: StockBucketSummary }

    `available = true` when at least one matching animal exists and
    the matching set has not been entirely sold/dispatched. A bucket
    with `total = 0` is reported as `available = false` so the BFF can
    show a "no stock" empty state.
    """
    animals = _fetch_animals(
        company_id,
        category=category,
        sex=sex,
        tropa=tropa,
        ingreso=ingreso,
    )
    total, con_caravana, sin_caravana = _summarize_animals(animals)

    bucket = {
        "total": total,
        "conCaravana": con_caravana,
        "sinCaravana": sin_caravana,
        "category": category or None,
        "sex": sex or None,
        "tropa": tropa or None,
        "ingreso": ingreso or None,
    }
    return {
        "available": total > 0,
        "bucket": _map_bucket(bucket),
    }


@frappe.whitelist()
def get_ledger(company_id, category=None, sex=None, from_date=None, to_date=None, page=1, limit=100):
    """Return the on-hand animals as a ledger of stock positions.

    Response shape:
      {
        entries: [StockLedgerEntry],
        page: int,
        limit: int,
        total: int (optional)
      }

    Each entry is sourced from an Animal row. The ledger is the current
    on-hand snapshot (no historical movement table exists yet), so
    every entry reports `movementType = "ingreso"` and `status =
    "confirmed"`. `from_date` / `to_date` filter on the
    `Livestock Intake` posting_date so the BFF can show "intakes in
    this period" as the ledger narrative.
    """
    page = max(cint(page), 1)
    limit = min(max(cint(limit), 1), 500)
    animals = _fetch_animals(company_id, category=category, sex=sex)

    # Resolve intake / herd-batch join for the entry's `tropa` and `ingreso` fields.
    herd_batch_ids = sorted(
        {str(a.get("current_herd_batch") or "") for a in animals if a.get("current_herd_batch")}
    )
    herd_batches = {}
    if herd_batch_ids:
        for row in frappe.get_all(
            "Herd Batch",
            filters=[["name", "in", herd_batch_ids]],
            fields=_existing_fields(
                "Herd Batch",
                ["name", "origin_document", "arrival_date", "company"],
            ),
            limit_page_length=max(len(herd_batch_ids), 1),
        ):
            herd_batches[str(row.get("name") or "")] = row

    entries = []
    for animal in animals:
        herd_batch_id = str(animal.get("current_herd_batch") or "")
        herd_batch = herd_batches.get(herd_batch_id, {})
        evento_date = str(herd_batch.get("arrival_date") or "")
        if from_date and evento_date and evento_date < str(from_date):
            continue
        if to_date and evento_date and evento_date > str(to_date):
            continue
        entry = {
            "id": str(animal.get("name") or ""),
            "documentType": "Livestock Intake",
            "documentId": str(herd_batch.get("origin_document") or ""),
            "movementType": "ingreso",
            "category": str(animal.get("current_category") or ""),
            "sex": str(animal.get("sex") or ""),
            "tropa": herd_batch_id or None,
            "ingreso": str(herd_batch.get("origin_document") or "") or None,
            "quantity": 1,
            "conCaravana": 0 if _is_placeholder_animal(animal) else 1,
            "sinCaravana": 1 if _is_placeholder_animal(animal) else 0,
            "status": "confirmed",
            "company": str(animal.get("company") or ""),
            "eventDate": evento_date,
            "observation": None,
        }
        entries.append(entry)

    total_count = len(entries)
    start = (page - 1) * limit
    end = start + limit
    paginated = entries[start:end]

    return {
        "entries": paginated,
        "page": page,
        "limit": limit,
        "total": total_count,
    }
