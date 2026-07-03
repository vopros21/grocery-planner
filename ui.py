"""
ui.py — minimal local web UI for reviewing parsed receipts.

Usage:
    python3 ui.py

Open http://localhost:5000 in your browser.
"""

import json
from pathlib import Path
from flask import Flask, render_template_string, request, redirect, url_for, jsonify

from store import load_pending, confirm_receipt, remove_pending

from analysis import planner_bp

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

  .cat-badge { font-size: 11px; background: #e8e8ed; border-radius: 4px;
               padding: 2px 6px; color: #3a3a3c; white-space: nowrap; }
  .flag { font-size: 11px; background: #ff9f0a22; color: #b25000;
          border-radius: 4px; padding: 2px 6px; white-space: nowrap; }

  input[type=number] { width: 80px; padding: 4px 6px; border: 1px solid #c7c7cc;
                       border-radius: 6px; font-size: 14px; }
  input[type=number]:focus { outline: 2px solid #007aff; border-color: transparent; }

  .price { font-variant-numeric: tabular-nums; text-align: right; }
  .discount { color: #30d158; font-size: 12px; }

  .actions { padding: 16px 20px; display: flex; gap: 10px; justify-content: flex-end;
             border-top: 1px solid #f0f0f0; background: #fafafa; }
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

<header>
  <h1>🛒 Grocer Review</h1>
  {% if receipts %}
  <span class="badge">{{ receipts|length }}</span>
  {% endif %}
</header>

{% if not receipts %}
<div class="empty">
  <h2>No receipts pending</h2>
  <p>Drop a PDF into the <code>inbox/</code> folder to get started.</p>
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

  <form method="POST" action="/confirm/{{ rid }}">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Product</th>
          <th>Category</th>
          <th style="text-align:center">Qty</th>
          <th style="text-align:right">Unit €</th>
          <th style="text-align:right">Total €</th>
          <th>Weight kg</th>
        </tr>
      </thead>
      <tbody>
        {% for item in items %}
        <tr {% if item.needs_weight %}class="needs-weight"{% endif %}>
          <td style="color:#6e6e73;font-size:12px">{{ loop.index }}</td>
          <td>
            {{ item.name }}
            {% if item.needs_weight %}
            <span class="flag">⚖ weight</span>
            {% endif %}
            {% if item.discount %}
            <br><span class="discount">-€{{ "%.2f"|format(item.discount) }} saved</span>
            {% endif %}
          </td>
          <td><span class="cat-badge">{{ item.category }}</span></td>
          <td style="text-align:center">{{ item.qty|int if item.qty == item.qty|int else item.qty }}</td>
          <td class="price">{{ "%.2f"|format(item.unit_price) }}</td>
          <td class="price">{{ "%.2f"|format(item.total_price) }}</td>
          <td>
            {% if item.needs_weight %}
            <input type="number" name="weight_{{ loop.index0 }}"
                   step="0.001" min="0" placeholder="0.000">
            {% else %}
            <span style="color:#c7c7cc">—</span>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>

    <div class="actions">
      <button type="button" class="btn btn-discard"
              onclick="discard('{{ rid }}')">Discard</button>
      <button type="submit" class="btn btn-confirm">✓ Confirm &amp; Save</button>
    </div>
  </form>
</div>
{% endfor %}

<div class="toast" id="toast"></div>

<script>
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
    return render_template_string(HTML, receipts=receipts)


@app.route("/confirm/<receipt_id>", methods=["POST"])
def confirm(receipt_id):
    pending = load_pending()
    if receipt_id not in pending:
        return redirect(url_for("index"))

    items = pending[receipt_id]["rows"]

    # Merge submitted weight values back into items
    for i, item in enumerate(items):
        weight_key = f"weight_{i}"
        val = request.form.get(weight_key, "").strip()
        if val:
            try:
                item["weight_kg"] = float(val)
            except ValueError:
                item["weight_kg"] = ""
        else:
            item["weight_kg"] = ""

    confirm_receipt(receipt_id, items)
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
