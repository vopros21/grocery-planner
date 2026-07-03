"""
store.py — flat-file persistence layer.

Files:
  data/items.csv      — confirmed grocery items (append-only)
  data/pending.json   — items waiting for review in the web UI
"""

import csv
import json
import os
from pathlib import Path

from github_sync import push_to_github

BASE = Path(__file__).parent / "data"
BASE.mkdir(exist_ok=True)

ITEMS_CSV = BASE / "items.csv"
PENDING_JSON = BASE / "pending.json"

FIELDNAMES = [
    "date", "store", "category", "name",
    "qty", "weight_kg", "unit_price", "total_price", "discount",
    "iva_band",
]


# ── Items CSV ────────────────────────────────────────────────────────

def append_items(items: list[dict]):
    """Append a list of confirmed item dicts to items.csv."""
    write_header = not ITEMS_CSV.exists() or ITEMS_CSV.stat().st_size == 0
    with open(ITEMS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for item in items:
            # Ensure weight_kg column exists (may be filled in review)
            row = {k: item.get(k, "") for k in FIELDNAMES}
            writer.writerow(row)


def load_items() -> list[dict]:
    """Return all confirmed items from CSV."""
    if not ITEMS_CSV.exists():
        return []
    with open(ITEMS_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── Pending review ───────────────────────────────────────────────────

def load_pending() -> dict:
    """Return {receipt_id: {meta: {...}, rows: [...]}} or {}."""
    if not PENDING_JSON.exists():
        return {}
    with open(PENDING_JSON, encoding="utf-8") as f:
        return json.load(f)


def save_pending(pending: dict):
    with open(PENDING_JSON, "w", encoding="utf-8") as f:
        json.dump(pending, f, indent=2, ensure_ascii=False)


def add_pending_receipt(receipt_id: str, meta: dict, items: list[dict]):
    """Queue a receipt for review."""
    pending = load_pending()
    pending[receipt_id] = {"meta": meta, "rows": items}
    save_pending(pending)


def confirm_receipt(receipt_id: str, confirmed_items: list[dict]):
    """Move a reviewed receipt from pending to items.csv."""
    pending = load_pending()
    if receipt_id in pending:
        del pending[receipt_id]
        save_pending(pending)
    append_items(confirmed_items)
    push_to_github(f"add receipt {receipt_id}")


def remove_pending(receipt_id: str):
    """Discard a pending receipt without saving."""
    pending = load_pending()
    if receipt_id in pending:
        del pending[receipt_id]
        save_pending(pending)
