"""
status_tracker.py - Application Status & Feedback Tracker
Extends history.json with application statuses and auto-parses recruiter emails.
"""
from __future__ import annotations

import json
import re
import tempfile
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

WORKSPACE_ROOT = Path(__file__).resolve().parent
HISTORY_PATH = WORKSPACE_ROOT / "history.json"

VALID_STATUSES = {"pending", "refused", "interview", "accepted", "withdrawn"}
STATUS_LABELS = {
    "pending": "Pending / Waiting",
    "refused": "Refused / Rejected",
    "interview": "Interview / Accepted",
    "accepted": "Interview / Accepted",
    "withdrawn": "Withdrawn",
}
STATUS_KEYWORDS = {
    "refused": ["absage", "rejected", "rejection", "unfortunately", "leider",
                "kein weiter", "no longer", "not selected", "nicht eingeladen",
                "absagen", "decline", "declined", "absagt"],
    "interview": ["interview", "gespräch", "einladung", "invite", "invitation",
                  "telefonat", "call", "meeting", "vorstellungsgespräch",
                  "einstellungsgespräch", "next steps", "nächste schritte",
                  "coding challenge", "test"],
    "accepted": ["offer", "angebot", "accepted", "angenommen", "willkommen",
                 "welcome", "contract", "vertrag", "sign", "unterschreiben"],
    "pending": ["received", "eingegangen", "bestätigt", "confirmed", "in prüfung",
                "under review", "bearbeitet"],
}


def load_history() -> Dict[str, Any]:
    if HISTORY_PATH.exists():
        with HISTORY_PATH.open("r", encoding="utf-8-sig") as f:
            return json.load(f)
    return {}


def save_history(history: Dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=HISTORY_PATH.parent, delete=False, suffix=".tmp",
    ) as tmp:
        json.dump(history, tmp, ensure_ascii=False, indent=2)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_path = Path(tmp.name)
    os.replace(temp_path, HISTORY_PATH)


def normalize_status(status: str) -> str:
    s = status.strip().lower()
    if s in VALID_STATUSES:
        return s
    for key, label in STATUS_LABELS.items():
        if s == label.lower():
            return key
    return "pending"


def parse_status_from_text(text: str) -> Tuple[str, float, List[str]]:
    if not text or not text.strip():
        return "pending", 0.0, []
    text_lower = text.lower()
    scores: Dict[str, int] = {s: 0 for s in VALID_STATUSES}
    matched: Dict[str, List[str]] = {s: [] for s in VALID_STATUSES}
    for status, keywords in STATUS_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                scores[status] += 1
                matched[status].append(kw)
    best_status = max(scores, key=scores.get)
    best_score = scores[best_status]
    total_matches = sum(scores.values())
    if total_matches == 0:
        return "pending", 0.0, []
    confidence = min(1.0, best_score / max(total_matches, 1))
    return best_status, confidence, matched[best_status]


def update_status(
    job_url: str,
    status: str,
    source: str = "manual",
    note: Optional[str] = None,
) -> Dict[str, Any]:
    status = normalize_status(status)
    history = load_history()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if job_url not in history:
        history[job_url] = {"processed_at": now, "status": "success"}
    entry = history[job_url]
    entry["application_status"] = status
    entry["application_status_label"] = STATUS_LABELS.get(status, status)
    entry["status_updated_at"] = now
    entry["status_source"] = source
    if note:
        entry["status_note"] = note
    save_history(history)
    return entry
def list_applications() -> List[Dict[str, Any]]:
    history = load_history()
    results: List[Dict[str, Any]] = []
    try:
        from job_analyzer import list_all_analyses
        analyses_by_url: Dict[str, Dict[str, Any]] = {}
        for a in list_all_analyses():
            analyses_by_url[a.get("job_url", "")] = a
    except Exception:
        analyses_by_url = {}
    for url, entry in history.items():
        record = {
            "url": url,
            "processed_at": entry.get("processed_at", ""),
            "pipeline_status": entry.get("status", "success"),
            "application_status": entry.get("application_status", "pending"),
            "application_status_label": entry.get(
                "application_status_label",
                STATUS_LABELS.get("pending", "Pending / Waiting"),
            ),
            "status_updated_at": entry.get("status_updated_at", ""),
            "status_source": entry.get("status_source", ""),
            "status_note": entry.get("status_note", ""),
        }
        if url in analyses_by_url:
            a = analyses_by_url[url]
            record["match_score"] = a.get("match_score", 0)
            record["tech_stack"] = a.get("tech_stack", [])
            record["key_requirements"] = a.get("key_requirements", [])
            record["job_type"] = a.get("job_type", "")
            record["location"] = a.get("location", "")
            record["analysis_summary"] = a.get("summary", "")
            record["analyzed_at"] = a.get("analyzed_at", "")
        else:
            record["match_score"] = 0
            record["tech_stack"] = []
            record["key_requirements"] = []
            record["job_type"] = ""
            record["location"] = ""
            record["analysis_summary"] = ""
            record["analyzed_at"] = ""
        results.append(record)
    status_order = {"accepted": 0, "interview": 1, "pending": 2, "refused": 3, "withdrawn": 4}
    results.sort(
        key=lambda r: (status_order.get(r["application_status"], 5), -r.get("match_score", 0))
    )
    return results


def get_metrics() -> Dict[str, Any]:
    apps = list_applications()
    total = len(apps)
    counts = {s: 0 for s in VALID_STATUSES}
    for a in apps:
        st = a["application_status"]
        counts[st] = counts.get(st, 0) + 1
    scores = [a.get("match_score", 0) for a in apps if a.get("match_score", 0) > 0]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0
    return {
        "total": total,
        "pending": counts.get("pending", 0),
        "refused": counts.get("refused", 0),
        "interview": counts.get("interview", 0),
        "accepted": counts.get("accepted", 0),
        "withdrawn": counts.get("withdrawn", 0),
        "avg_match_score": avg_score,
    }
