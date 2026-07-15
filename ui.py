"""
ui.py — minimal local web UI for reviewing parsed receipts.

Usage:
    python3 ui.py

Open http://localhost:5000 in your browser.
"""

import json
import uuid
from pathlib import Path
from datetime import date, datetime
from flask import Flask, render_template_string, request, redirect, url_for, jsonify

from store import load_pending, confirm_receipt, remove_pending, load_items, add_pending_receipt

from analysis import (
    planner_bp,
    normalize_name,
    load_category_overrides,
    save_category_overrides,
    get_known_categories,
)

app = Flask(__name__)
app.register_blueprint(planner_bp)

# ── HTML template ────────────────────────────────────────────────────

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Grocer Review</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #f5f5f7; color: #1d1d1f; font-size: 15px; }

  header { background: #fff; border-bottom: 1px solid #d2d2d7;
           padding: 14px 24px; display: flex; align-items: center; gap: 16px; }
  header h1 { font-size: 18px; font-weight: 600; }
  .badge { background: #ff3b30; color: #fff; border-radius: 10px;
           padding: 2px 8px; font-size: 12px; font-weight: 700; }

  .empty { text-align: center; padding: 80px 24px; color: #6e6e73; }
  .empty h2 { font-size: 22px; margin-bottom: 8px; }

  .manual-new { max-width: 860px; margin: 20px auto 0; padding: 14px 20px;
                background: #fff; border-radius: 12px; box-shadow: 0 1px 4px rgba(0,0,0,.1);
                display: flex; align-items: center; gap: 10px; }
  .manual-new strong { margin-right: auto; font-size: 14px; }
  .manual-new select, .manual-new input[type=date] {
    padding: 7px 10px; border: 1px solid #c7c7cc; border-radius: 6px; font-size: 13px; }

  .receipt-card { background: #fff; border-radius: 12px; margin: 20px auto;
                  max-width: 860px; box-shadow: 0 1px 4px rgba(0,0,0,.1); overflow: hidden; }
  .card-header { padding: 16px 20px; border-bottom: 1px solid #f0f0f0;
                 display: flex; justify-content: space-between; align-items: center; }
  .card-header h2 { font-size: 16px; font-weight: 600; }
  .card-meta { font-size: 13px; color: #6e6e73; }

  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; padding: 8px 12px; font-size: 12px; font-weight: 600;
       color: #6e6e73; text-transform: uppercase; letter-spacing: .04em;
       border-bottom: 1px solid #f0f0f0; background: #fafafa; }
  td { padding: 9px 12px; border-bottom: 1px solid #f7f7f7; vertical-align: middle; }
  tr:last-child td { border-bottom: none; }
  tr.needs-weight { background: #fff8e6; }
  tr.editing { background: #f0f7ff; }
  tr.manual-row { background: #f0fff4; }

  .cat-badge { font-size: 11px; background: #e8e8ed; border-radius: 4px;
               padding: 2px 6px; color: #3a3a3c; white-space: nowrap; }
  .flag { font-size: 11px; background: #ff9f0a22; color: #b25000;
          border-radius: 4px; padding: 2px 6px; white-space: nowrap; }
  .discount { color: #30d158; font-size: 12px; }

  .edit-field { border: 1px solid transparent; background: transparent;
                border-radius: 6px; padding: 4px 6px; font-size: 14px;
                font-family: inherit; color: inherit; width: 100%; }
  .edit-field[readonly] { color: #1d1d1f; cursor: default; }
  .edit-field:not([readonly]) { border-color: #007aff; background: #fff; }
  .edit-field:focus { outline: none; }
  input.edit-field[type=number] { width: 70px; }
  .name-cell .edit-field { width: 220px; }
  .cat-cell .edit-field { width: 140px; }
  .price-cell .edit-field { width: 70px; text-align: right; }

  .price { font-variant-numeric: tabular-nums; }

  .row-actions { white-space: nowrap; }
  .icon-btn { border: none; background: none; cursor: pointer; font-size: 14px;
              padding: 4px 6px; border-radius: 6px; color: #6e6e73; }
  .icon-btn:hover { background: #f0f0f0; }
  .icon-btn.del:hover { background: #ffeceb; color: #ff3b30; }

  .actions { padding: 16px 20px; display: flex; gap: 10px; justify-content: space-between;
             align-items: center; border-top: 1px solid #f0f0f0; background: #fafafa; }
  .add-item-btn { padding: 7px 14px; border: 1px dashed #c7c7cc; border-radius: 8px;
                  font-size: 13px; background: none; cursor: pointer; color: #3a3a3c; }
  .add-item-btn:hover { border-color: #007aff; color: #007aff; }
  .btn-group { display: flex; gap: 10px; }
  .btn { padding: 9px 20px; border: none; border-radius: 8px; font-size: 14px;
         font-weight: 600; cursor: pointer; }
  .btn-confirm { background: #007aff; color: #fff; }
  .btn-confirm:hover { background: #0062cc; }
  .btn-discard { background: #f2f2f7; color: #3a3a3c; }
  .btn-discard:hover { background: #e5e5ea; }

  .toast { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
           background: #1d1d1f; color: #fff; padding: 12px 24px; border-radius: 10px;
           font-size: 14px; opacity: 0; transition: opacity .3s; pointer-events: none; }
  .toast.show { opacity: 1; }
</style>
</head>
<body>

<datalist id="categoryOptions">
  {% for c in known_categories %}
  <option value="{{ c }}">
  {% endfor %}
</datalist>

<header>
  <h1>🛒 Grocer Review</h1>
  {% if receipts %}
  <span class="badge">{{ receipts|length }}</span>
  {% endif %}
</header>

<div class="manual-new">
  <form method="POST" action="/manual/new" style="display:flex;align-items:center;gap:10px;width:100%;">
    <strong>+ New manual receipt</strong>
    <select name="store">
      <option value="Lidl">Lidl</option>
      <option value="Continente">Continente</option>
      <option value="Other">Other</option>
    </select>
    <input type="date" name="date" value="{{ today }}">
    <button type="submit" class="btn btn-confirm" style="padding:7px 16px;">Create</button>
  </form>
</div>

{% if not receipts %}
<div class="empty">
  <h2>No receipts pending</h2>
  <p>Drop a PDF into the <code>inbox/</code> folder, or start a manual receipt above.</p>
</div>
{% endif %}

{% for rid, receipt in receipts.items() %}
{% set meta = receipt.meta %}
{% set items = receipt.rows %}
<div class="receipt-card" id="card-{{ rid }}">
  <div class="card-header">
    <div>
      <h2>{{ meta.store }} — {{ meta.date }}</h2>
      <div class="card-meta">{{ meta.filename }} · {{ meta.item_count }} items · Total ~€{{ "%.2f"|format(meta.total) }}</div>
    </div>
  </div>

  <form method="POST" action="/confirm/{{ rid }}" class="receipt-form" data-rid="{{ rid }}">
    <input type="hidden" name="row_indices" value="">
    <table data-next-idx="{{ items|length }}">
      <thead>
        <tr>
          <th>#</th>
          <th>Product</th>
          <th>Category</th>
          <th style="text-align:center">Qty</th>
          <th style="text-align:right">Unit €</th>
          <th style="text-align:right">Total €</th>
          <th>Weight kg</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {% for item in items %}
        <tr class="item-row {% if item.needs_weight %}needs-weight{% endif %}" data-idx="{{ loop.index0 }}">
          <td style="color:#6e6e73;font-size:12px">{{ loop.index }}</td>
          <td class="name-cell">
            <input type="text" class="edit-field" name="name_{{ loop.index0 }}"
                   value="{{ item.name }}" data-default-readonly="true" readonly>
            {% if item.needs_weight %}<span class="flag">⚖ weight</span>{% endif %}
            {% if item.discount %}<br><span class="discount">-€{{ "%.2f"|format(item.discount) }} saved</span>{% endif %}
          </td>
          <td class="cat-cell">
            <input type="text" class="edit-field" name="category_{{ loop.index0 }}"
                   list="categoryOptions" placeholder="uncategorized"
                   value="{{ item.category or item.suggested_category or '' }}"
                   data-default-readonly="{{ 'true' if item.category else 'false' }}"
                   {% if item.category %}readonly{% endif %}>
          </td>
          <td style="text-align:center">
            <input type="number" step="0.01" class="edit-field" name="qty_{{ loop.index0 }}"
                   value="{{ item.qty|int if item.qty == item.qty|int else item.qty }}"
                   data-default-readonly="true" readonly oninput="recalcTotal(this)">
          </td>
          <td class="price-cell">
            <input type="number" step="0.01" class="edit-field" name="unit_price_{{ loop.index0 }}"
                   value="{{ '%.2f'|format(item.unit_price) }}" data-default-readonly="true" readonly oninput="recalcTotal(this)">
          </td>
          <td class="price-cell">
            <input type="number" step="0.01" class="edit-field" name="total_price_{{ loop.index0 }}"
                   value="{{ '%.2f'|format(item.total_price) }}" data-default-readonly="true" readonly>
          </td>
          <td>
            <input type="number" step="0.001" min="0" class="edit-field" name="weight_{{ loop.index0 }}"
                   value="{{ item.weight_kg }}" placeholder="0.000"
                   data-default-readonly="{{ 'false' if item.needs_weight else 'true' }}"
                   {% if not item.needs_weight %}readonly{% endif %}>
          </td>
          <td class="row-actions">
            <button type="button" class="icon-btn edit-btn" onclick="toggleEdit(this)">✎</button>
            <button type="button" class="icon-btn del" onclick="removeRow(this)">✕</button>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>

    <div class="actions">
      <button type="button" class="add-item-btn"
              onclick="addManualRow(document.querySelector('#card-{{ rid }} table'))">+ Add item</button>
      <div class="btn-group">
        <button type="button" class="btn btn-discard"
                onclick="discard('{{ rid }}')">Discard</button>
        <button type="submit" class="btn btn-confirm">✓ Confirm &amp; Save</button>
      </div>
    </div>
  </form>
</div>
{% endfor %}

<div class="toast" id="toast"></div>

<script>
function recalcTotal(el) {
  const row = el.closest('tr');
  const qtyField = row.querySelector('input[name^="qty_"]');
  const unitField = row.querySelector('input[name^="unit_price_"]');
  const totalField = row.querySelector('input[name^="total_price_"]');
  const qty = parseFloat(qtyField.value) || 0;
  const unit = parseFloat(unitField.value) || 0;
  totalField.value = (qty * unit).toFixed(2);
}

function toggleEdit(btn) {
  const row = btn.closest('tr');
  const editing = row.classList.toggle('editing');
  row.querySelectorAll('.edit-field').forEach(inp => {
    inp.readOnly = editing ? false : (inp.dataset.defaultReadonly === 'true');
  });
  btn.textContent = editing ? '✓' : '✎';
}

function removeRow(btn) {
  const row = btn.closest('tr');
  if (row.classList.contains('manual-row') || confirm("Remove this item from the receipt?")) {
    row.remove();
  }
}

function addManualRow(table) {
  const nextIdx = parseInt(table.dataset.nextIdx, 10);
  table.dataset.nextIdx = nextIdx + 1;

  const tr = document.createElement('tr');
  tr.className = 'item-row manual-row';
  tr.dataset.idx = nextIdx;
  tr.innerHTML = `
    <td style="color:#6e6e73;font-size:12px">+</td>
    <td class="name-cell"><input type="text" class="edit-field" name="name_${nextIdx}" placeholder="Product name"></td>
    <td class="cat-cell"><input type="text" class="edit-field" name="category_${nextIdx}" list="categoryOptions" placeholder="category"></td>
    <td style="text-align:center"><input type="number" step="0.01" class="edit-field" name="qty_${nextIdx}" value="1" oninput="recalcTotal(this)"></td>
    <td class="price-cell"><input type="number" step="0.01" class="edit-field" name="unit_price_${nextIdx}" placeholder="0.00" oninput="recalcTotal(this)"></td>
    <td class="price-cell"><input type="number" step="0.01" class="edit-field" name="total_price_${nextIdx}" placeholder="0.00"></td>
    <td><input type="number" step="0.001" min="0" class="edit-field" name="weight_${nextIdx}" placeholder="0.000"></td>
    <td class="row-actions"><button type="button" class="icon-btn del" onclick="removeRow(this)">✕</button></td>
  `;
  table.querySelector('tbody').appendChild(tr);
}

document.querySelectorAll('.receipt-form').forEach(form => {
  form.addEventListener('submit', () => {
    const idxs = Array.from(form.querySelectorAll('.item-row')).map(r => r.dataset.idx);
    form.querySelector('input[name=row_indices]').value = idxs.join(',');
  });
});

function discard(rid) {
  if (!confirm("Discard this receipt? Items will not be saved.")) return;
  fetch("/discard/" + rid, {method:"POST"})
    .then(() => {
      document.getElementById("card-" + rid).remove();
      showToast("Receipt discarded");
      if (!document.querySelector(".receipt-card")) {
        location.reload();
      }
    });
}
function showToast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2500);
}
// Auto-refresh every 8s to pick up new receipts
setInterval(() => {
  fetch("/api/pending-count")
    .then(r => r.json())
    .then(d => { if (d.count !== {{ receipts|length }}) location.reload(); });
}, 8000);
</script>
</body>
</html>
"""

# ── Routes ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    receipts = load_pending()
    category_overrides = load_category_overrides()

    # Pre-fill a category guess for any item that arrived with a blank
    # category (always Lidl) if we've already categorized this same
    # product before — still editable, just saves re-typing every time.
    for receipt in receipts.values():
        for item in receipt["rows"]:
            if not item.get("category"):
                item["suggested_category"] = category_overrides.get(
                    normalize_name(item.get("name", "")), ""
                )

    known_categories = get_known_categories(load_items(), category_overrides)
    return render_template_string(
        HTML, receipts=receipts, known_categories=known_categories,
        today=date.today().isoformat(),
    )


@app.route("/manual/new", methods=["POST"])
def manual_new():
    store = request.form.get("store", "Other").strip() or "Other"
    date_str = request.form.get("date", "").strip() or datetime.today().strftime("%Y-%m-%d")

    receipt_id = f"manual-{uuid.uuid4().hex[:10]}"
    meta = {
        "store": store,
        "date": date_str,
        "filename": "manual entry",
        "item_count": 0,
        "total": 0.0,
    }
    add_pending_receipt(receipt_id, meta, [])
    return redirect(url_for("index"))


@app.route("/confirm/<receipt_id>", methods=["POST"])
def confirm(receipt_id):
    pending = load_pending()
    if receipt_id not in pending:
        return redirect(url_for("index"))

    meta = pending[receipt_id]["meta"]
    old_items = pending[receipt_id]["rows"]

    row_indices_raw = request.form.get("row_indices", "").strip()
    if row_indices_raw:
        row_indices = [int(i) for i in row_indices_raw.split(",") if i.strip() != ""]
    else:
        row_indices = list(range(len(old_items)))

    def _float(key, default=0.0):
        val = request.form.get(key, "").strip()
        if val == "":
            return default
        try:
            return float(val)
        except ValueError:
            return default

    confirmed_items = []
    new_overrides = {}

    for idx in row_indices:
        old = old_items[idx] if idx < len(old_items) else {}

        name = request.form.get(f"name_{idx}", old.get("name", "")).strip()
        if not name:
            continue  # blank manual row never filled in — skip silently

        category = request.form.get(f"category_{idx}", old.get("category", "")).strip()
        qty = _float(f"qty_{idx}", old.get("qty", 1.0) or 1.0)
        unit_price = _float(f"unit_price_{idx}", old.get("unit_price", 0.0) or 0.0)
        total_price = _float(f"total_price_{idx}", old.get("total_price", 0.0) or 0.0)

        weight_val = request.form.get(f"weight_{idx}", "").strip()
        weight_kg = weight_val if weight_val != "" else old.get("weight_kg", "")

        item = {
            "name": name,
            "qty": qty,
            "unit_price": unit_price,
            "total_price": total_price,
            "discount": old.get("discount", 0.0),
            "category": category,
            "needs_weight": old.get("needs_weight", False),
            "weight_kg": weight_kg,
            "store": meta.get("store", old.get("store", "")),
            "date": meta.get("date", old.get("date", "")),
            "iva_band": old.get("iva_band", ""),
        }
        confirmed_items.append(item)

        if category:
            new_overrides[normalize_name(name)] = category

    if new_overrides:
        overrides = load_category_overrides()
        overrides.update(new_overrides)
        save_category_overrides(overrides)

    confirm_receipt(receipt_id, confirmed_items)
    return redirect(url_for("index"))


@app.route("/discard/<receipt_id>", methods=["POST"])
def discard(receipt_id):
    remove_pending(receipt_id)
    return jsonify({"ok": True})


@app.route("/api/pending-count")
def pending_count():
    return jsonify({"count": len(load_pending())})


if __name__ == "__main__":
    import os
    port = int(os.environ.get("GROCER_UI_PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
