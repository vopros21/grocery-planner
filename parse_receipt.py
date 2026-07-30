"""
parse_receipt.py — extract line items from Continente (and future Lidl) PDF receipts.

Returns a list of dicts:
  {
    "name": str,         # raw product name from receipt
    "qty": float,        # unit count (1 if single)
    "unit_price": float, # price per unit
    "total_price": float,# qty * unit_price
    "category": str,     # section header (Talho, Peixaria, …)
    "needs_weight": bool,# True if weight must be filled in manually
    "store": str,        # "Continente" | "Lidl"
    "date": str,         # ISO date "YYYY-MM-DD"
  }
"""

import re
import pdfplumber
from datetime import datetime

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

STORE_PATTERNS = {
    "Continente": re.compile(r"CONTINENTE", re.I),
    "Lidl": re.compile(r"LIDL", re.I),
}

# Items sold by weight have these suffixes in Continente receipts
WEIGHT_SUFFIXES = re.compile(r"\bLS\b|\bFRAC\b|\bKG\b", re.I)

# Section header lines (end with ":")
CATEGORY_RE = re.compile(r"^([A-ZÀ-Ú][^\n:]{2,40}):$")

# Standard single-price line: starts with (A/B/C) + name + price
ITEM_LINE_RE = re.compile(
    r"^\(([ABC])\)\s+(.+?)\s+([\d]+[,.][\d]{2})$"
)

# Item line with no price yet (next line will be "N X price  total")
ITEM_NO_PRICE_RE = re.compile(r"^\(([ABC])\)\s+(.+)$")

# Multi-unit continuation line:  "2 X 1,15  2,30"
MULTI_UNIT_RE = re.compile(
    r"^(\d+)\s+[Xx]\s+([\d]+[,.][\d]{2})\s+([\d]+[,.][\d]{2})$"
)

POUPANCA_RE = re.compile(r"^POUPANCA\s+([\d]+[,.][\d]{2})$", re.I)
SUBTOTAL_RE = re.compile(r"^SUBTOTAL\b", re.I)
# Some receipts (e.g. Wells pharmacy, still under the Continente umbrella) have
# no SUBTOTAL line at all — they go straight to TOTAL A PAGAR instead. Also
# guard on the %IVA breakdown table header as a second, format-agnostic
# fallback in case a future receipt variant has neither marker.
TOTAL_RE = re.compile(r"^TOTAL\s+A\s+PAGAR\b", re.I)
IVA_TABLE_HEADER_RE = re.compile(r"^%IVA\b", re.I)

# Date formats seen across receipts:
#   Continente: "13/06/2026" (DD/MM/YYYY)
#   Lidl:       "Data de Venda:   2026-07-02" (already ISO YYYY-MM-DD)
DATE_DDMMYYYY_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
DATE_ISO_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def _parse_price(s: str) -> float:
    return float(s.replace(",", "."))


def _detect_store(lines: list[str]) -> str:
    for line in lines[:15]:
        for store, pat in STORE_PATTERNS.items():
            if pat.search(line):
                return store
    return "Unknown"


def _detect_date(lines: list[str]) -> str:
    for line in lines[:20]:
        m = DATE_DDMMYYYY_RE.search(line)
        if m:
            try:
                return datetime.strptime(m.group(1), "%d/%m/%Y").strftime("%Y-%m-%d")
            except ValueError:
                pass
        m = DATE_ISO_RE.search(line)
        if m:
            return m.group(1)
    return datetime.today().strftime("%Y-%m-%d")


def extract_text_dedup(page, x_tolerance: float = 1, y_tolerance: float = 1) -> str:
    """
    Rebuild a pdfplumber page's text char-by-char, de-colliding glyphs whose
    bounding boxes overlap on the same line, instead of using page.extract_text()
    directly.

    Bug this fixes: on some Lidl receipts a long product name overruns its
    column and the printer stacks a price/qty fragment directly on top of the
    tail of the name (same line, same x-range) instead of in its own column —
    e.g. "SteamBrew Cerveja Artesanal" + "0,99" both occupy x=142-166 on the
    same line. page.extract_text() sorts purely by x-position, so it interleaves
    the two overlapping runs character-by-character: "Arte0s,a9n9al". Confirmed
    via page.chars inspection against a real receipt (250002288620260730718462.pdf)
    — this is a genuine defect in the source PDF's character placement, not a
    pdfplumber sorting quirk or an OCR issue.

    Fix: group chars into lines by top (y), sort by x0, and when two chars'
    boxes overlap, keep the letter and drop the digit/punctuation intruder —
    the real price/qty is always also present later on the line as its own
    separated token, so nothing is lost by dropping the intruder itself (see
    LIDL_MULTI_UNIT_NO_UNITPRICE_RE below for how the dropped unit price is
    recovered from total_price / qty instead).

    Verified to be byte-identical to page.extract_text() on every line of a
    real receipt where no collision occurs — safe drop-in replacement.
    """
    from collections import defaultdict

    lines_by_top = defaultdict(list)
    for c in page.chars:
        lines_by_top[round(c["top"] / y_tolerance) * y_tolerance].append(c)

    out_lines = []
    for top in sorted(lines_by_top.keys()):
        cs = sorted(lines_by_top[top], key=lambda c: c["x0"])
        kept = []
        for c in cs:
            if kept and c["x0"] < kept[-1]["x1"] - 0.5:
                prev = kept[-1]
                if prev["text"].isalpha() and not c["text"].isalpha():
                    continue  # drop digit/punct intruder, keep the letter
                if c["text"].isalpha() and not prev["text"].isalpha():
                    kept[-1] = c  # letter wins over digit/punct
                continue  # both same type colliding: keep first, drop second
            kept.append(c)

        line_text, prev_x1 = "", None
        for c in kept:
            if prev_x1 is not None and c["x0"] - prev_x1 > x_tolerance * 2:
                line_text += " "
            line_text += c["text"]
            prev_x1 = c["x1"]
        out_lines.append(line_text)

    return "\n".join(out_lines)


# -------------------------------------------------------------------
# Lidl-specific patterns
# -------------------------------------------------------------------
# Lidl format differs from Continente in several ways:
#   - No per-item category headers — items aren't grouped by section
#   - IVA band comes AFTER the price, not in parens before the name:
#       "GELADO CHUPA CHUPA COLA            3,69 A"
#   - Weight items print actual weight on the line right below the item —
#     no manual weight entry needed, unlike Continente's LS/FRAC/KG items:
#       "LIMAO                              0,83 B"
#       "  0,296 kg x 2,79   EUR/kg"
#   - The listed item price is the PRE-discount (shelf) price. A DESCONTO
#     line below it gives the euro amount to subtract:
#       "SALSICHAS FRESCAS DE PORCO         3,50 A"
#       "  0,588 kg x 5,95   EUR/kg"
#       "    DESCONTO 20%                  -0,71"
#     This is the opposite convention from Continente's POUPANCA, where
#     VALOR is already the paid price. To keep total_price meaning "amount
#     actually paid" consistently across stores, the discount is subtracted
#     here (Continente's is not — see POUPANCA handling above).
#   - No SUBTOTAL line; item list ends at "Total    10,19"

LIDL_ITEM_RE = re.compile(
    r"^(.+?)\s+([\d]+[,.][\d]{2})\s+([A-Z])$"
)

# Multi-unit line: "CROQUETE DE CARNE 0,85 x 4    3,40 A"
#   name, then UNIT_PRICE, "x", QTY, then TOTAL_PRICE, then band.
# Checked BEFORE LIDL_ITEM_RE — otherwise LIDL_ITEM_RE's non-greedy name
# group happily swallows "0,85 x 4" into the name (since it only anchors
# on the final PRICE + BAND at end of line), leaving qty stuck at 1.0 and
# unit_price wrongly equal to total_price.
LIDL_MULTI_UNIT_RE = re.compile(
    r"^(.+?)\s+([\d]+[,.][\d]{2})\s+[Xx]\s+(\d+)\s+([\d]+[,.][\d]{2})\s+([A-Z])$"
)

# Fallback for the name/price-collision bug that extract_text_dedup() fixes
# (see its docstring): when the colliding unit-price token fully overlapped a
# letter, dedup keeps the letter and the intruding digits are dropped
# entirely — so the unit price is missing from the line, e.g.
#   "SteamBrew Cerveja Artesanal x 2 1,98 A"   (no standalone unit price)
# instead of
#   "SteamBrew Cerveja Artesanal 0,99 x 2 1,98 A"
# Checked after LIDL_MULTI_UNIT_RE fails to match. unit_price is recovered
# as total_price / qty.
LIDL_MULTI_UNIT_NO_UNITPRICE_RE = re.compile(
    r"^(.+?)\s+[Xx]\s+(\d+)\s+([\d]+[,.][\d]{2})\s+([A-Z])$"
)

LIDL_WEIGHT_RE = re.compile(
    r"^([\d]+[,.]\d+)\s+kg\s+[Xx]\s+([\d]+[,.][\d]{2})\s+EUR/kg$", re.I
)

LIDL_DISCOUNT_RE = re.compile(
    r"^DESCONTO\s+\d+%\s+-?([\d]+[,.][\d]{2})$", re.I
)

# App/photo receipts (e.g. LidlPlus digital receipt) use a plain "Promoção"
# line instead of PDF receipts' "DESCONTO N%" — same meaning (amount to
# subtract from the item just above), different wording/no percent shown.
LIDL_PROMOCAO_RE = re.compile(
    r"^Promo[cç][aã]o\s+-?([\d]+[,.][\d]{2})$", re.I
)

# Bottle/can deposit ("Depósito 0.10") — the 0,10€ is refundable via the
# in-store return machine, not money actually spent on groceries. Excluded
# from the item list entirely rather than categorised, per Mike.
LIDL_DEPOSIT_RE = re.compile(r"^Dep[oó]sito\b", re.I)

# End-of-items marker: "Total    10,19" — must NOT match "Total Poupança"
# (the savings-summary line further down), so it requires a number right
# after "Total".
LIDL_TOTAL_RE = re.compile(r"^Total\s+[\d]+[,.][\d]{2}$")


def parse_lidl(lines: list[str], store: str, date: str) -> list[dict]:
    items = []

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        # Stop at the Total line or MULTIBANCO — everything below is
        # payment/tax-summary info, not items. Lidl receipts have no
        # SUBTOTAL marker at all.
        if LIDL_TOTAL_RE.match(line) or line.upper().startswith("MULTIBANCO"):
            break

        # ── Weight continuation line — attach to the item just added ──
        m = LIDL_WEIGHT_RE.match(line)
        if m:
            weight_kg = _parse_price(m.group(1))
            if items:
                items[-1]["weight_kg"] = weight_kg
            continue

        # ── DESCONTO line — subtract from the item just added ─────────
        m = LIDL_DISCOUNT_RE.match(line)
        if m:
            discount = _parse_price(m.group(1))
            if items:
                items[-1]["discount"] += discount
                items[-1]["total_price"] -= discount
                items[-1]["unit_price"] = items[-1]["total_price"]
            continue

        # ── Promoção line (app/photo receipts) — same handling as DESCONTO ──
        m = LIDL_PROMOCAO_RE.match(line)
        if m:
            discount = _parse_price(m.group(1))
            if items:
                items[-1]["discount"] += discount
                items[-1]["total_price"] -= discount
                items[-1]["unit_price"] = items[-1]["total_price"]
            continue

        # ── Bottle/can deposit — not a purchase, skip entirely ─────────
        if LIDL_DEPOSIT_RE.match(line):
            continue

        # ── Multi-unit item line: "NAME  UNIT_PRICE x QTY  TOTAL_PRICE  BAND" ──
        # Must be checked before the plain item pattern below (see comment
        # on LIDL_MULTI_UNIT_RE) or the "x N" gets absorbed into the name.
        m = LIDL_MULTI_UNIT_RE.match(line)
        if m:
            name, unit_price_str, qty_str, total_price_str, iva = m.groups()
            items.append({
                "name": name.strip(),
                "qty": float(qty_str),
                "unit_price": _parse_price(unit_price_str),
                "total_price": _parse_price(total_price_str),
                "discount": 0.0,
                "category": "",
                "needs_weight": False,
                "weight_kg": "",
                "store": store,
                "date": date,
                "iva_band": iva,
            })
            continue

        # ── Multi-unit item line, unit price missing (collision-dedup case) ──
        m = LIDL_MULTI_UNIT_NO_UNITPRICE_RE.match(line)
        if m:
            name, qty_str, total_price_str, iva = m.groups()
            qty = float(qty_str)
            total_price = _parse_price(total_price_str)
            items.append({
                "name": name.strip(),
                "qty": qty,
                "unit_price": round(total_price / qty, 2),
                "total_price": total_price,
                "discount": 0.0,
                "category": "",
                "needs_weight": False,
                "weight_kg": "",
                "store": store,
                "date": date,
                "iva_band": iva,
            })
            continue

        # ── Standard item line: "NAME   PRICE  BAND" ───────────────────
        m = LIDL_ITEM_RE.match(line)
        if m:
            name, price_str, iva = m.group(1).strip(), m.group(2), m.group(3)
            total_price = _parse_price(price_str)
            items.append({
                "name": name,
                "qty": 1.0,
                "unit_price": total_price,
                "total_price": total_price,
                "discount": 0.0,
                # Lidl receipts print no per-item category/section headers.
                # Left empty for now — plan is to map product names to
                # categories via a lookup dict once one exists (Mike's
                # basket is fairly repetitive, so it shouldn't need to be
                # large). Revisit here once that dict is built.
                "category": "",
                "needs_weight": False,  # weight (if any) comes pre-filled below
                "weight_kg": "",
                "store": store,
                "date": date,
                "iva_band": iva,
            })
            continue

        # Anything else (headers, dashes, totals table rows) — ignore

    return items


# -------------------------------------------------------------------
# Main parser
# -------------------------------------------------------------------

def parse_continente(lines: list[str], store: str, date: str) -> list[dict]:
    items = []
    current_category = "Uncategorised"
    pending_item = None          # carries (iva, name) waiting for multi-unit line
    last_item_idx = None         # index into items[] of the most recently added item

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        # Stop at SUBTOTAL, TOTAL A PAGAR, or the %IVA breakdown table header —
        # everything below any of these is payment/tax-summary info, not items.
        # Not every receipt has SUBTOTAL (e.g. Wells goes straight to TOTAL A
        # PAGAR), so all three markers are checked.
        if SUBTOTAL_RE.match(line) or TOTAL_RE.match(line) or IVA_TABLE_HEADER_RE.match(line):
            break

        # ── Section header ───────────────────────────────────────────
        m = CATEGORY_RE.match(line)
        if m:
            current_category = m.group(1).strip()
            pending_item = None
            continue

        # ── POUPANCA (discount) — attach to last item ────────────────
        # VALOR on the receipt is already the discounted price paid.
        # POUPANCA is the saving on top — record it separately, don't subtract.
        m = POUPANCA_RE.match(line)
        if m:
            discount = _parse_price(m.group(1))
            if items:
                items[-1]["discount"] = items[-1].get("discount", 0.0) + discount
            pending_item = None
            continue

        # ── Multi-unit continuation  "2 X 1,15  2,30" ───────────────
        if pending_item:
            m = MULTI_UNIT_RE.match(line)
            if m:
                qty = int(m.group(1))
                unit_price = _parse_price(m.group(2))
                total_price = _parse_price(m.group(3))
                iva, name = pending_item
                needs_weight = bool(WEIGHT_SUFFIXES.search(name))
                items.append({
                    "name": name,
                    "qty": float(qty),
                    "unit_price": unit_price,
                    "total_price": total_price,
                    "discount": 0.0,
                    "category": current_category,
                    "needs_weight": needs_weight,
                    "store": store,
                    "date": date,
                    "iva_band": iva,
                })
                pending_item = None
                continue

        # ── Standard item line with price ────────────────────────────
        m = ITEM_LINE_RE.match(line)
        if m:
            iva, name, price_str = m.group(1), m.group(2).strip(), m.group(3)
            total_price = _parse_price(price_str)
            needs_weight = bool(WEIGHT_SUFFIXES.search(name))
            items.append({
                "name": name,
                "qty": 1.0,
                "unit_price": total_price,
                "total_price": total_price,
                "discount": 0.0,
                "category": current_category,
                "needs_weight": needs_weight,
                "store": store,
                "date": date,
                "iva_band": iva,
            })
            pending_item = None
            continue

        # ── Item line without price (multi-unit, next line follows) ──
        m = ITEM_NO_PRICE_RE.match(line)
        if m:
            pending_item = (m.group(1), m.group(2).strip())
            continue

        # Anything else — reset pending
        pending_item = None

    return items


# -------------------------------------------------------------------
# Image OCR (macOS Vision — the same engine behind Live Text / Preview)
# -------------------------------------------------------------------
# Deliberately NOT tesseract: no `brew install` needed (Vision.framework
# ships with every Mac), and it's noticeably better on real-world photos/
# app-screenshots — exactly the LidlPlus-app-receipt case that motivated
# this. Requires `pip install pyobjc-framework-Vision pyobjc-framework-Quartz`
# (pure pip, no system package). macOS-only — will raise ImportError on
# Linux/Windows.

def extract_text_from_image(path: str) -> list[str]:
    """Run macOS Vision text recognition on an image and return lines,
    ordered top-to-bottom, left-to-right the way the receipt reads.

    Vision returns each detected text region as a separate observation —
    on a receipt with columns (item name on the left, price+band on the
    right), that means the name and its price come back as TWO separate
    observations even though they're one printed line. A naive sort by
    y-coordinate alone doesn't merge them back together (and small y
    jitter between columns can even reorder same-row items). So instead:
    cluster observations into rows by y-proximity, then within each row
    sort left-to-right by x and join with a space. That reconstructs each
    line the way a human reads it — no regex changes needed downstream.
    """
    import Quartz
    import Vision
    from Foundation import NSURL

    url = NSURL.fileURLWithPath_(path)
    image_source = Quartz.CGImageSourceCreateWithURL(url, None)
    cg_image = Quartz.CGImageSourceCreateImageAtIndex(image_source, 0, None)

    results = []  # (bounding_box, text)

    def handler(request, error):
        if error is not None:
            return
        for observation in request.results():
            candidate = observation.topCandidates_(1)[0]
            results.append((observation.boundingBox(), candidate.string()))

    request = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(handler)
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(["pt-PT", "en-US"])
    request.setUsesLanguageCorrection_(True)

    req_handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, {})
    success, error = req_handler.performRequests_error_([request], None)
    if not success:
        raise RuntimeError(f"Vision OCR failed: {error}")

    # Vision's boundingBox origin is bottom-left, normalized 0..1.
    # Sort top-to-bottom first so rows get built in reading order.
    results.sort(key=lambda r: -(r[0].origin.y + r[0].size.height / 2))

    rows = []  # each: {"y": float, "height": float, "items": [(x, text), ...]}
    for bbox, text in results:
        y_center = bbox.origin.y + bbox.size.height / 2
        height = bbox.size.height
        placed = False
        for row in rows:
            # Same row if the vertical centers are within ~60% of a
            # text-line height of each other — tight enough to keep
            # genuinely separate lines apart, loose enough to absorb
            # the column-to-column jitter that caused issues above.
            if abs(row["y"] - y_center) < max(height, row["height"]) * 0.6:
                row["items"].append((bbox.origin.x, text))
                placed = True
                break
        if not placed:
            rows.append({"y": y_center, "height": height, "items": [(bbox.origin.x, text)]})

    lines = []
    for row in rows:
        ordered = sorted(row["items"], key=lambda t: t[0])
        lines.append(" ".join(t[1] for t in ordered))
    return lines


def parse_image(path: str) -> list[dict]:
    lines = extract_text_from_image(path)
    store = _detect_store(lines)
    date = _detect_date(lines)

    if store == "Continente":
        return parse_continente(lines, store, date)
    if store == "Lidl":
        return parse_lidl(lines, store, date)

    raise NotImplementedError(f"Parser not yet implemented for store: {store}")


# -------------------------------------------------------------------
# Entry point
# -------------------------------------------------------------------

def parse_pdf(path: str) -> list[dict]:
    with pdfplumber.open(path) as pdf:
        lines = []
        for page in pdf.pages:
            text = extract_text_dedup(page) or ""
            lines.extend(text.splitlines())

    store = _detect_store(lines)
    date = _detect_date(lines)

    if store == "Continente":
        return parse_continente(lines, store, date)

    if store == "Lidl":
        return parse_lidl(lines, store, date)

    raise NotImplementedError(f"Parser not yet implemented for store: {store}")


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


def parse_receipt(path: str) -> list[dict]:
    """Unified entry point — dispatches on extension. Use this from
    watcher.py instead of calling parse_pdf/parse_image directly."""
    ext = path.lower().rsplit(".", 1)[-1]
    if f".{ext}" in IMAGE_EXTENSIONS:
        return parse_image(path)
    return parse_pdf(path)


if __name__ == "__main__":
    import sys, json
    items = parse_receipt(sys.argv[1])
    print(json.dumps(items, indent=2, ensure_ascii=False))
