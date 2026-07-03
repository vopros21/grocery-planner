"""
watcher.py — watch inbox/ for new PDFs and queue them for review.

Usage:
    python3 watcher.py

Drop any Continente (or future Lidl) PDF into the inbox/ folder.
The watcher parses it immediately, moves it to processed/, and adds
it to data/pending.json for review in the web UI.
"""

import os
import shutil
import sys
import time
import uuid
import json
from pathlib import Path
from datetime import datetime

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from parse_receipt import parse_pdf
from store import add_pending_receipt

BASE      = Path(__file__).parent
INBOX     = BASE / "inbox"
PROCESSED = BASE / "processed"

INBOX.mkdir(exist_ok=True)
PROCESSED.mkdir(exist_ok=True)

UI_PORT = os.environ.get("GROCER_UI_PORT", "5001")
UI_URL  = f"http://localhost:{UI_PORT}"


def link(url: str, text: str | None = None) -> str:
    """Wrap a URL in an OSC 8 hyperlink escape sequence so it renders as
    clickable in terminals that support it (Terminal.app, iTerm2, etc)."""
    text = text or url
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def process_pdf(path: Path):
    # macOS can fire multiple filesystem events for a single drop/move.
    # If a prior event already moved this file to processed/, bail out
    # quietly instead of failing on a missing file.
    if not path.exists():
        return

    print(f"[{datetime.now():%H:%M:%S}] Processing: {path.name}")
    try:
        items = parse_pdf(str(path))
    except Exception as e:
        print(f"  ✗ Parse failed: {e}")
        return

    if not items:
        print("  ✗ No items extracted — skipping.")
        return

    receipt_id = f"{path.stem}_{uuid.uuid4().hex[:6]}"
    store = items[0]["store"]
    date  = items[0]["date"]
    total = sum(i["total_price"] for i in items)

    meta = {
        "receipt_id": receipt_id,
        "filename": path.name,
        "store": store,
        "date": date,
        "item_count": len(items),
        "total": round(total, 2),
        "parsed_at": datetime.now().isoformat(),
    }

    add_pending_receipt(receipt_id, meta, items)

    # Move to processed/
    dest = PROCESSED / path.name
    if dest.exists():
        dest = PROCESSED / f"{path.stem}_{receipt_id[:6]}{path.suffix}"
    shutil.move(str(path), str(dest))

    needs_review = sum(1 for i in items if i.get("needs_weight"))
    print(f"  ✓ {len(items)} items | {needs_review} need weight | queued as {receipt_id}")
    print(f"  → Open {link(UI_URL)} to review")


class InboxHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() == ".pdf":
            # Small delay — wait for file to finish copying
            time.sleep(0.5)
            process_pdf(path)

    def on_moved(self, event):
        # Handles drag-and-drop on macOS which fires moved, not created
        if event.is_directory:
            return
        path = Path(event.dest_path)
        if path.suffix.lower() == ".pdf":
            time.sleep(0.5)
            process_pdf(path)


def main():
    # Also process any PDFs already sitting in inbox (e.g. on restart)
    existing = list(INBOX.glob("*.pdf"))
    if existing:
        print(f"Found {len(existing)} existing PDF(s) in inbox — processing...")
        for pdf in existing:
            process_pdf(pdf)

    observer = Observer()
    observer.schedule(InboxHandler(), str(INBOX), recursive=False)
    observer.start()
    print(f"Watching {INBOX} for new PDFs… (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
