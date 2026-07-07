"""
analysis.py — Shopping trends & bulk-buy planner for grocer.

Adds a "/planner" page to the existing Flask app (ui.py) via a Blueprint.
Read-only with respect to items.csv — it only writes to a small new file,
data/stockable_overrides.json, to remember which products you've manually
marked as stockable/not-stockable.

INTEGRATION — add these two lines to ui.py (near the other imports / app setup):

    from analysis import planner_bp
    app.register_blueprint(planner_bp)

Then drop templates/planner.html into the same templates/ folder ui.py
already uses (or point template_folder below at wherever that is).

Nothing in store.py, watcher.py, or the existing items.csv schema changes.
"""

import csv
import json
import re
from collections import defaultdict
from datetime import datetime, date, timedelta
from pathlib import Path

from flask import Blueprint, render_template, request, redirect, url_for

from github_sync import push_to_github

BASE_DIR = Path(__file__).resolve().parent
ITEMS_CSV = BASE_DIR / "data" / "items.csv"
STOCKABLE_CONFIG = BASE_DIR / "data" / "stockable_overrides.json"
CATEGORY_CONFIG = BASE_DIR / "data" / "category_overrides.json"

# Matches the known %IVA-table parsing artifact rows (e.g. "23,00% 11,34 2,61")
# so they're excluded from the averages without needing the cleanup script run first.
CORRUPTED_NAME_RE = re.compile(r"^\d+,\d+%\s+\d+,\d+\s+\d+,\d+$")

# Continente section headers that are almost always freezer/pantry-stable.
STOCKABLE_CATEGORIES = {
    "talho",              # butcher — meat, freezable
    "peixaria",           # fishmonger — freezable
    "congelados",         # frozen foods
    "mercearia salgada",  # savory dry grocery — rice, pasta, beans, canned goods
    "mercearia doce",     # sweet dry grocery
    "conservas",
}

NOT_STOCKABLE_CATEGORIES = {
    "frutas e legumes",
    "padaria",
    "pastelaria",
    "laticinios",
    "laticínios",
}

# Fallback keyword guess — mainly for Lidl items, whose category is always "".
STOCKABLE_KEYWORDS = [
    "feijao", "feijão", "grao", "grão", "lentilha", "arroz", "massa", "esparguete",
    "atum", "sardinha", "conserva", "enlatado", "congelad",
    "azeite", "oleo", "óleo", "acucar", "açucar", "açúcar", "farinha", "cereais",
]
PERISHABLE_KEYWORDS = [
    "alface", "tomate", "banana", "maca", "maçã", "laranja", "pao ", "pão ",
    "iogurte", "queijo fresco", "salada", "cogumelo", "morango", "uva",
]


def normalize_name(name: str) -> str:
    n = name.strip().lower()
    n = re.sub(r"\s+", " ", n)
    return n


def guess_stockable(category: str, name: str) -> bool:
    cat = (category or "").strip().lower()
    if cat in STOCKABLE_CATEGORIES:
        return True
    if cat in NOT_STOCKABLE_CATEGORIES:
        return False
    n = normalize_name(name)
    if any(k in n for k in PERISHABLE_KEYWORDS):
        return False
    if any(k in n for k in STOCKABLE_KEYWORDS):
        return True
    return False  # default: don't suggest stockpiling unless there's a reason to


def load_items():
    if not ITEMS_CSV.exists():
        return []
    rows = []
    with ITEMS_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if CORRUPTED_NAME_RE.match((row.get("name") or "").strip()):
                continue
            rows.append(row)
    return rows


def load_overrides():
    if STOCKABLE_CONFIG.exists():
        return json.loads(STOCKABLE_CONFIG.read_text(encoding="utf-8"))
    return {}


def save_overrides(overrides):
    STOCKABLE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    STOCKABLE_CONFIG.write_text(json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Category overrides ──────────────────────────────────────────────
# Same pattern as stockable overrides above: {normalized_name: category}.
# Lets you assign a Continente-style category to a Lidl item (whose
# receipts never print one) once, permanently — every past and future
# row for that product picks it up at read time, no CSV rewrite needed.

def load_category_overrides():
    if CATEGORY_CONFIG.exists():
        return json.loads(CATEGORY_CONFIG.read_text(encoding="utf-8"))
    return {}


def save_category_overrides(overrides):
    CATEGORY_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CATEGORY_CONFIG.write_text(json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8")


def get_known_categories(rows, category_overrides=None):
    """Distinct category names seen in items.csv, unioned with any values
    already used in category_overrides.json — for the dropdown/datalist so
    it always matches Continente's real category vocabulary."""
    cats = {(r.get("category") or "").strip() for r in rows}
    if category_overrides:
        cats.update(v.strip() for v in category_overrides.values())
    cats.discard("")
    return sorted(cats)


def _to_float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


# Minimum real-world spread required before we trust a monthly-rate estimate.
# Buying 2 packs of the same product in one trip must not look like "buys this daily" —
# so we count distinct shopping DATES, not raw CSV rows, and require both enough
# distinct dates and enough elapsed time before extrapolating a rate.
MIN_PURCHASE_DATES = 2
MIN_SPAN_DAYS = 14

DAYS_PER_MONTH = 30.44

# A product with no purchase in this many months is considered "stale" —
# you've likely switched to a different brand/item. Hidden from the planner
# by default (toggle-able), but never deleted from items.csv itself.
STALE_MONTHS_DEFAULT = 6.0


def compute_product_stats(rows, overrides, category_overrides=None, buffer_months=2.0, window_months=3.0,
                           stale_months=STALE_MONTHS_DEFAULT):
    """Group rows by normalized product name; estimate a rolling-window monthly
    consumption rate and suggested buy qty.

    The rate is a rolling average over the last `window_months` — older purchases
    fall out of the average once you have more than window_months of history for
    a product. If a product has LESS history than that (e.g. bought for the first
    time 5 weeks ago), we average over however much history actually exists
    instead of diluting against a window that hasn't happened yet.
    """
    category_overrides = category_overrides or {}

    groups = defaultdict(list)
    for r in rows:
        key = normalize_name(r.get("name", ""))
        if not key:
            continue
        groups[key].append(r)

    today = date.today()
    window_days = window_months * DAYS_PER_MONTH
    stale_cutoff_days = stale_months * DAYS_PER_MONTH
    products = []

    for key, items in groups.items():
        dated_items = []
        for r in items:
            try:
                d = datetime.strptime(r["date"], "%Y-%m-%d").date()
            except (ValueError, KeyError):
                continue
            dated_items.append((d, r))
        if not dated_items:
            continue

        all_dates = [d for d, _ in dated_items]
        # Distinct shopping dates this product was bought on, across ALL history —
        # this defines whether we've observed a real purchase pattern at all.
        # Multiple units bought on the same date count toward total_units but are
        # one restock event, not evidence of buying frequency.
        distinct_dates = sorted(set(all_dates))
        purchase_dates_count = len(distinct_dates)
        first_date, last_date = distinct_dates[0], distinct_dates[-1]
        overall_span_days = (last_date - first_date).days

        unit_type = "kg" if any(_to_float(r.get("weight_kg")) > 0 for r in items) else "un"

        def units_for(r):
            wk = _to_float(r.get("weight_kg"))
            return wk if unit_type == "kg" and wk > 0 else _to_float(r.get("qty"))

        total_units = sum(units_for(r) for r in items)
        total_spend = sum(_to_float(r.get("total_price")) for r in items)

        display_name = max((r["name"] for r in items), key=len)

        category_override = category_overrides.get(key)
        if category_override:
            category = category_override
        else:
            categories = [r.get("category", "") for r in items if r.get("category")]
            category = max(set(categories), key=categories.count) if categories else ""

        enough_history = purchase_dates_count >= MIN_PURCHASE_DATES and overall_span_days >= MIN_SPAN_DAYS

        avg_units_per_month = None
        avg_spend_per_month = None
        if enough_history:
            # Rolling window, with warm-up: if we have less than window_months of
            # history for this product, use all of it rather than the full window
            # (otherwise a product only tracked for 3 weeks would look 4x rarer
            # than it really is once divided by a 3-month window).
            window_start = today - timedelta(days=window_days)
            effective_start = max(window_start, first_date)
            effective_span_days = max((today - effective_start).days, MIN_SPAN_DAYS)
            effective_span_months = effective_span_days / DAYS_PER_MONTH

            window_units = sum(units_for(r) for d, r in dated_items if d >= effective_start)
            window_spend = sum(_to_float(r.get("total_price")) for d, r in dated_items if d >= effective_start)

            avg_units_per_month = window_units / effective_span_months
            avg_spend_per_month = window_spend / effective_span_months

        stockable = overrides.get(key)
        if stockable is None:
            stockable = guess_stockable(category, display_name)

        days_since_last = (today - last_date).days
        suggested_buy = None
        running_low = False
        if stockable and avg_units_per_month:
            suggested_buy = round(avg_units_per_month * buffer_months, 2 if unit_type == "kg" else 0)
            expected_cycle_days = overall_span_days / (purchase_dates_count - 1)
            running_low = days_since_last >= expected_cycle_days

        stale = days_since_last > stale_cutoff_days

        products.append({
            "key": key,
            "name": display_name,
            "category": category or "(uncategorized — Lidl)",
            "stores_seen": sorted(set(r.get("store", "") for r in items if r.get("store"))),
            "purchase_dates_count": purchase_dates_count,
            "unit_type": unit_type,
            "total_units": round(total_units, 2),
            "total_spend": round(total_spend, 2),
            "avg_units_per_month": round(avg_units_per_month, 2) if avg_units_per_month else None,
            "avg_spend_per_month": round(avg_spend_per_month, 2) if avg_spend_per_month else None,
            "last_date": last_date.isoformat(),
            "days_since_last": days_since_last,
            "enough_history": enough_history,
            "stockable": stockable,
            "suggested_buy": suggested_buy,
            "running_low": running_low,
            "stale": stale,
        })

    return products


def compute_monthly_category_spend(rows, category_overrides=None):
    category_overrides = category_overrides or {}
    monthly = defaultdict(lambda: defaultdict(float))
    for r in rows:
        try:
            d = datetime.strptime(r["date"], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            continue
        month_key = f"{d.year}-{d.month:02d}"
        override = category_overrides.get(normalize_name(r.get("name", "")))
        cat = override or r.get("category") or "(uncategorized)"
        monthly[month_key][cat] += _to_float(r.get("total_price"))
    return monthly


planner_bp = Blueprint("planner", __name__, template_folder="templates")


@planner_bp.route("/planner")
def planner():
    buffer_months = float(request.args.get("buffer", 2.0))
    window_months = float(request.args.get("window", 3.0))
    stale_months = float(request.args.get("stale", STALE_MONTHS_DEFAULT))
    show_stale = request.args.get("show_stale") == "true"
    sort_by = request.args.get("sort", "category")

    rows = load_items()
    overrides = load_overrides()
    category_overrides = load_category_overrides()
    known_categories = get_known_categories(rows, category_overrides)
    all_products = compute_product_stats(rows, overrides, category_overrides, buffer_months, window_months,
                                          stale_months)
    monthly_spend = compute_monthly_category_spend(rows, category_overrides)

    stale_count = sum(1 for p in all_products if p["stale"])
    products = all_products if show_stale else [p for p in all_products if not p["stale"]]

    months_sorted = sorted(monthly_spend.keys())
    all_categories = sorted({cat for m in monthly_spend.values() for cat in m.keys()})
    chart_series = {
        cat: [round(monthly_spend[m].get(cat, 0.0), 2) for m in months_sorted]
        for cat in all_categories
    }

    shopping_list = sorted(
        [p for p in products if p["stockable"] and p["suggested_buy"]],
        key=lambda p: (not p["running_low"], p["category"], p["name"]),
    )

    if sort_by == "spend":
        products.sort(key=lambda p: p["total_spend"], reverse=True)
    elif sort_by == "last":
        products.sort(key=lambda p: p["days_since_last"], reverse=True)
    else:
        products.sort(key=lambda p: (p["category"], p["name"]))

    return render_template(
        "planner.html",
        products=products,
        shopping_list=shopping_list,
        buffer_months=buffer_months,
        window_months=window_months,
        stale_months=stale_months,
        show_stale=show_stale,
        stale_count=stale_count,
        months=months_sorted,
        chart_categories=all_categories,
        chart_series=chart_series,
        sort_by=sort_by,
        known_categories=known_categories,
    )


@planner_bp.route("/planner/toggle-stockable", methods=["POST"])
def toggle_stockable():
    key = request.form["key"]
    new_value = request.form["value"] == "true"
    overrides = load_overrides()
    overrides[key] = new_value
    save_overrides(overrides)
    push_to_github(f"toggle stockable: {key} -> {new_value}")
    return redirect(url_for(
        "planner.planner",
        buffer=request.form.get("buffer", 2.0),
        window=request.form.get("window", 3.0),
        stale=request.form.get("stale", STALE_MONTHS_DEFAULT),
        show_stale=request.form.get("show_stale", "false"),
        sort=request.form.get("sort", "category"),
    ))


@planner_bp.route("/planner/set-category", methods=["POST"])
def set_category():
    key = request.form["key"]
    category = request.form.get("category", "").strip()
    overrides = load_category_overrides()
    if category:
        overrides[key] = category
    else:
        # blank submission clears the override, falling back to the
        # receipt-derived guess (or "(uncategorized — Lidl)") again
        overrides.pop(key, None)
    save_category_overrides(overrides)
    push_to_github(f"set category: {key} -> {category or '(cleared)'}")
    return redirect(url_for(
        "planner.planner",
        buffer=request.form.get("buffer", 2.0),
        window=request.form.get("window", 3.0),
        stale=request.form.get("stale", STALE_MONTHS_DEFAULT),
        show_stale=request.form.get("show_stale", "false"),
        sort=request.form.get("sort", "category"),
    ))
