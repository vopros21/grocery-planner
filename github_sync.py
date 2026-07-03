"""
github_sync.py — pushes data/items.csv and data/stockable_overrides.json to
GitHub after every write, so the static planner (planner-static.html, hosted
on GitHub Pages) always has current data.

SETUP (one-time):
    cd ~/Desktop/grocer/grocer
    git init                      # if this folder isn't already a repo
    git remote add origin https://github.com/vopros21/grocery-planner.git
    git branch -M main
    git add data/items.csv data/stockable_overrides.json
    git commit -m "initial data"
    git push -u origin main

After that, this module just needs `git push` to work non-interactively —
easiest is an SSH remote or a stored credential helper, since confirm_receipt()
and toggle_stockable() call this synchronously and can't prompt for a password.

Failures here are swallowed (logged, not raised) — a GitHub push failing
should never break a receipt confirmation or an override toggle on your Mac.
"""

import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Paths (relative to BASE_DIR) to keep in sync with GitHub.
SYNCED_PATHS = [
    "data/items.csv",
    "data/stockable_overrides.json",
]


def push_to_github(commit_message: str = "update grocer data") -> bool:
    """Stage, commit, and push the synced data files. Returns True on success,
    False on any failure (network down, no changes, not a repo yet, etc.) —
    never raises, so callers don't need to wrap this in try/except."""
    try:
        existing = [p for p in SYNCED_PATHS if (BASE_DIR / p).exists()]
        if not existing:
            return False

        subprocess.run(
            ["git", "add", *existing],
            cwd=BASE_DIR, check=True, capture_output=True, text=True,
        )

        commit = subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=BASE_DIR, capture_output=True, text=True,
        )
        # Non-zero exit here usually just means "nothing to commit" — not a real error.
        if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr).lower():
            print(f"[github_sync] commit warning: {commit.stderr.strip()}")

        push = subprocess.run(
            ["git", "push"],
            cwd=BASE_DIR, capture_output=True, text=True,
        )
        if push.returncode != 0:
            print(f"[github_sync] push failed: {push.stderr.strip()}")
            return False

        return True

    except Exception as e:
        print(f"[github_sync] sync error: {e}")
        return False
