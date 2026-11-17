#!/usr/bin/env python3
"""
Reset all history.json entries with application_status='needs_manual'
back to 'pending' so they become eligible for re-processing.

Skips entries with protected statuses (submitted, interview, offer, accepted,
refused, withdrawn, skipped) per the same rules as cleanup_data.py.

Usage:
    python reset_needs_manual.py           # dry run (prints what would change)
    python reset_needs_manual.py --apply   # write the changes
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

JOB_DIR = Path(__file__).resolve().parent
HISTORY_PATH = JOB_DIR / "history.json"
BACKUP_DIR = JOB_DIR / "_backup_cleanup"

PROTECTED_STATUSES = {
    "submitted", "interview", "offer", "accepted", "refused", "withdrawn", "skipped",
}


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(path: Path, data: Dict[str, Any]) -> None:
    BACKUP_DIR.mkdir(exist_ok=True)
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, BACKUP_DIR / f"{path.stem}.{stamp}{path.suffix}")
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def main() -> int:
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="Reset needs_manual entries back to pending for re-processing")
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = ap.parse_args()

    history = load_json(HISTORY_PATH)
    to_reset: Dict[str, str] = {}

    for url, entry in history.items():
        current = entry.get("application_status", "")
        if current == "needs_manual":
            to_reset[url] = current

    print(f"history.json: {len(history)} total entries")
    print(f"  needs_manual entries to reset: {len(to_reset)}")
    for url, current in to_reset.items():
        entry = history[url]
        company = entry.get("company", "?")
        title = entry.get("job_title", "?")
        print(f"  - {company} | {title} [{current}]")

    if not to_reset:
        print("\nNothing to reset.")
        return 0

    if not args.apply:
        print("\nDRY RUN - nothing written. Re-run with --apply to make these changes.")
        return 0

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for url in to_reset:
        entry = history[url]
        entry["application_status"] = "pending"
        entry["application_status_label"] = "Pending / Waiting"
        entry["status_updated_at"] = now
        entry["status_source"] = "manual_reset"
        # Clear submission-specific fields so the pipeline treats it fresh
        entry.pop("submitted_at", None)
        entry.pop("submission_note", None)
        entry.pop("automation_blocker", None)
        history[url] = entry

    save_json(HISTORY_PATH, history)
    print(f"\nDone. Reset {len(to_reset)} entry(ies) from needs_manual -> pending.")
    print(f"Backup saved in {BACKUP_DIR.name}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
