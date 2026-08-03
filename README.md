# grocer.py — receipt → CSV pipeline

Automatically extracts grocery items from Continente/Lidl PDF/PNG receipts into a flat CSV for analysis.

## Setup (once)

```bash
pip install pdfplumber watchdog flask
```

## File layout

```
grocer/
├── start.sh          ← run this
├── watcher.py        ← watches inbox/, parses PDFs
├── ui.py             ← local web review UI (port 5000)
├── parse_receipt.py  ← PDF → items parser
├── store.py          ← CSV / JSON persistence
│
├── inbox/            ← DROP PDFs HERE
├── processed/        ← PDFs move here after parsing
└── data/
    ├── items.csv     ← all confirmed grocery items
    └── pending.json  ← receipts awaiting review
```

## Daily use

```bash
cd grocer
./start.sh
```

1. Drop a PDF receipt into `inbox/`
2. Watcher detects it, parses it, moves it to `processed/`
3. Open **http://localhost:5000** in your browser
4. Review the items — fill in **weight (kg)** for meat/fish/produce sold by weight
5. Click **Confirm & Save** → items appended to `data/items.csv`

## CSV columns

| Column       | Notes                                      |
|--------------|--------------------------------------------|
| date         | ISO date from receipt (YYYY-MM-DD)         |
| store        | Continente / Lidl                          |
| category     | Section from receipt (Talho, Peixaria, …)  |
| name         | Product name as printed on receipt         |
| qty          | Unit count (1, 2, 6, …)                   |
| weight_kg    | Filled in review for LS/FRAC items         |
| unit_price   | Price per unit before discount             |
| total_price  | Actual amount paid (after discount)        |
| discount     | Amount saved on this item                  |
| iva_band     | A (6%) / B (13%) / C (23%)                |

## Extending for Lidl

Add a `parse_lidl()` function in `parse_receipt.py` and call it when `store == "Lidl"`. The store detection already looks for "LIDL" in the receipt header.

## Analysing the data

Open `data/items.csv` in Numbers/Excel or query with Python:

```python
import pandas as pd
df = pd.read_csv("data/items.csv", parse_dates=["date"])

# Most expensive categories
df.groupby("category")["total_price"].sum().sort_values(ascending=False)

# Price history of a product
df[df["name"].str.contains("FRANGO")][["date","name","total_price"]]
```
