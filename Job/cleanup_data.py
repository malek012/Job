#!/usr/bin/env python3
"""
cleanup_data.py - sanitize history.json and targets.json.

Default run is a DRY RUN that only prints what would change.

    python cleanup_data.py                        # show test/invalid records
    python cleanup_data.py --apply                # remove them (backup first)
    python cleanup_data.py --reset-missing-docs   # also list history entries whose PDFs are gone
    python cleanup_data.py --reset-missing-docs --apply
                                                  # ...and drop them from history so
                                                  # "Generate Docs" rebuilds them

What counts as a test / invalid record (same rules for both files):
  * StepStone URLs whose job id is in a known fabricated range (TEST_ID_RANGES)
  * company or title that is a placeholder (TestCompany, Testfirma, Unknown+Unknown)
  * URLs that are not job postings at all (account / booking / login pages)

--reset-missing-docs never touches an application that was already acted on
(submitted, interview, offer, accepted, refused, withdrawn, skipped).

Both files keep their schema: targets.json stays a JSON array of job objects,
history.json stays a JSON object keyed by URL. Writes are atomic and a
timestamped copy of each changed file is stored in _backup_cleanup/.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

JOB_DIR = Path(__file__).resolve().parent
HISTORY_PATH = JOB_DIR / "history.json"
TARGETS_PATH = JOB_DIR / "targets.json"
APPLICATIONS_DIR = JOB_DIR / "_applications"
BACKUP_DIR = JOB_DIR / "_backup_cleanup"

# Fabricated StepStone ids seeded during development (inclusive ranges).
TEST_ID_RANGES: List[Tuple[int, int]] = [(13900000, 13900099)]
PLACEHOLDER_NAMES = {"testcompany", "testfirma", "testfirma gmbh", "test company", "example", "example gmbh"}
NON_JOB_URL_PATTERNS = [
    r"stellenwerk\.de/account/",
    r"/(login|signin|sign-in|register|anmelden|registrieren)(/|\?|$)",
]
# Statuses that mean "a human or an employer already acted" -> never auto-reset.
PROTECTED_STATUSES = {
    "submitted", "interview", "offer", "accepted", "refused", "withdrawn", "skipped",
}


def stepstone_id(url: str) -> Optional[int]:
    m = re.search(r"--(\d+)-inline\.html", url)
    return int(m.group(1)) if m else None


def test_reason(url: str, company: str = "", title: str = "") -> Optional[str]:
    """Return why a record is test/invalid data, or None if it looks real."""
    if not url or not re.match(r"https?://", url):
        return "missing or invalid URL"
    sid = stepstone_id(url)
    if sid is not None and any(lo <= sid <= hi for lo, hi in TEST_ID_RANGES):
        return f"fabricated StepStone id {sid}"
    c, t = (company or "").strip().lower(), (title or "").strip().lower()
    if c in PLACEHOLDER_NAMES or t in PLACEHOLDER_NAMES:
        return "placeholder company/title"
    if c in ("", "unknown") and t in ("", "unknown"):
        return "no company and no title"
    for pat in NON_JOB_URL_PATTERNS:
        if re.search(pat, url, re.I):
            return "URL is not a job posting"
    return None


def docs_on_disk(entry: Dict[str, Any]) -> bool:
    folder = entry.get("folder_name") or ""
    if not folder or any(x in folder for x in ("/", "\\", "..")):
        return False
    d = APPLICATIONS_DIR / folder
    if not d.is_dir():
        return False
    names = {p.name.lower() for p in d.iterdir() if p.is_file()}  # case-insensitive like Windows
    return "cv.pdf" in names and "anschreiben.pdf" in names


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
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


def plan(reset_missing: bool) -> Dict[str, Any]:
    history: Dict[str, Any] = load_json(HISTORY_PATH, {})
    targets_raw = load_json(TARGETS_PATH, [])
    if not isinstance(history, dict):
        raise ValueError("history.json must be a JSON object keyed by URL")
    if not isinstance(targets_raw, list):
        raise ValueError("targets.json must be a JSON array")

    drop_history: Dict[str, str] = {}
    for url, e in history.items():
        reason = test_reason(url, e.get("company", ""), e.get("job_title", ""))
        if reason:
            drop_history[url] = reason
        elif reset_missing and not docs_on_disk(e) and e.get("application_status") not in PROTECTED_STATUSES:
            drop_history[url] = "PDFs missing on disk -> reset for regeneration"

    drop_targets: Dict[int, str] = {}
    seen: set = set()
    for i, job in enumerate(targets_raw):
        url = job.get("url", "") if isinstance(job, dict) else ""
        reason = test_reason(url, job.get("company", ""), job.get("job_title", "")) if isinstance(job, dict) else "not an object"
        if not reason and url in seen:
            reason = "duplicate URL"
        if reason:
            drop_targets[i] = reason
        else:
            seen.add(url)

    return {
        "history": history, "targets": targets_raw,
        "drop_history": drop_history, "drop_targets": drop_targets,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Remove test/invalid records from history.json and targets.json")
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    ap.add_argument("--reset-missing-docs", action="store_true",
                    help="also drop un-acted history entries whose PDFs are missing, so they regenerate")
    args = ap.parse_args()

    p = plan(args.reset_missing_docs)
    history, targets = p["history"], p["targets"]

    print(f"history.json : {len(history)} entries, removing {len(p['drop_history'])}")
    for url, reason in p["drop_history"].items():
        e = history[url]
        print(f"  - [{reason}] {e.get('company', '?')} | {e.get('job_title', '?')}")
    print(f"targets.json : {len(targets)} entries, removing {len(p['drop_targets'])}")
    for i, reason in p["drop_targets"].items():
        j = targets[i] if isinstance(targets[i], dict) else {}
        print(f"  - [{reason}] {j.get('company', '?')} | {j.get('job_title', '?')}")

    if not args.apply:
        print("\nDRY RUN - nothing written. Re-run with --apply to make these changes.")
        return 0

    if p["drop_history"]:
        save_json(HISTORY_PATH, {u: e for u, e in history.items() if u not in p["drop_history"]})
    if p["drop_targets"]:
        save_json(TARGETS_PATH, [j for i, j in enumerate(targets) if i not in p["drop_targets"]])
    print(f"\nDone. Backups of changed files are in {BACKUP_DIR.name}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
