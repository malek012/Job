#!/usr/bin/env python3
"""
Job Applier - Control Center API Server (FastAPI).

Serves the dashboard from Job/static/ and PDFs from Job/_applications/.
JSON endpoints drive the existing modules:
  - job_scraper        (live job discovery)
  - batch_processor    (CV + cover-letter generation, run as a subprocess)
  - apply_browser_agent (form filling / submission of APPROVED jobs only)
  - history.json / targets.json state

Application status flow:
    pending  --approve-->  approved  --execute-->  submitted | awaiting_approval | needs_manual | failed
    pending  --reject--->  skipped

Usage:
    python api_server.py [--port 7788]

Environment:
    JOB_APPLIER_AUTO_SUBMIT=1   let the browser agent click the final submit
                                button (default: fill the form and stop).
    JOB_APPLIER_HEADED=1        run the browser visibly; with auto-submit off the
                                filled form stays open for you to submit by hand.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

_JOB_DIR = Path(__file__).resolve().parent
if str(_JOB_DIR) not in sys.path:
    sys.path.insert(0, str(_JOB_DIR))

from batch_processor import load_history, load_targets, save_history  # noqa: E402
from doc_quality import letter_problem  # noqa: E402

# ── Paths ─────────────────────────────────────────────────────────────────────
STATIC_DIR = _JOB_DIR / "static"
APPLICATIONS_DIR = _JOB_DIR / "_applications"
APPLICATIONS_DIR.mkdir(parents=True, exist_ok=True)  # StaticFiles needs it to exist

SETTINGS_PATH = _JOB_DIR / "settings.json"
# Execute settings. Environment variables give the first-run defaults; after that
# the dashboard switches (stored in settings.json) decide.
SETTINGS: Dict[str, bool] = {
    # click the final submit button once every gate has passed
    "auto_submit": os.getenv("JOB_APPLIER_AUTO_SUBMIT", "0") == "1",
    # visible browser + persistent profile; pause for the person on captcha / login, then continue
    "assist": os.getenv("JOB_APPLIER_ASSIST", os.getenv("JOB_APPLIER_HEADED", "0")) == "1",
    # tick required privacy / consent checkboxes
    "accept_privacy": os.getenv("JOB_APPLIER_ACCEPT_PRIVACY", "0") == "1",
    # send Bewerbung.eml right away when an employer takes applications by e-mail
    "auto_send_email": os.getenv("JOB_APPLIER_AUTO_SEND_EMAIL", "0") == "1",
}
SEND_PAUSE_S = 20   # spacing between automatic e-mails
try:
    if SETTINGS_PATH.exists():
        _saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8-sig"))
        SETTINGS.update({k: bool(v) for k, v in _saved.items() if k in SETTINGS})
except Exception as _exc:  # noqa: BLE001
    print(f"settings.json unreadable, using defaults: {_exc}")

ATTENTION: Optional[str] = None   # what the person has to do in the browser window right now
AUTO_CYCLES = 3
GENERATE_TIMEOUT_S = 1800
DISCOVER_TIMEOUT_S = 900

STATUS_LABELS = {
    "pending": "Pending Review",
    "approved": "Approved",
    "skipped": "Skipped (by me)",
    "awaiting_approval": "Form Filled / Not Submitted",
    "needs_manual": "Needs Manual Application",
    "submitted": "Submitted",
    "failed": "Submission Failed",
    "dropped": "Dropped (Posting Gone / Docs Failed)",
    "interview": "Interview",
    "offer": "Offer",
    "accepted": "Accepted",
    "refused": "Refused by Employer",
    "withdrawn": "Withdrawn",
}

# Statuses that mean "a human or employer already acted" -> never auto-override
PROTECTED_STATUSES = {
    "submitted", "interview", "offer", "accepted", "refused", "withdrawn", "skipped",
}

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="Job Applier Control Center", version="1.1.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/applications", StaticFiles(directory=str(APPLICATIONS_DIR)), name="applications")

# ── Log buffer ────────────────────────────────────────────────────────────────
LOG_BUFFER: List[str] = []
LOG_MAX_LINES = 300
_log_lock = threading.Lock()


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    with _log_lock:
        LOG_BUFFER.append(line)
        if len(LOG_BUFFER) > LOG_MAX_LINES:
            del LOG_BUFFER[:-LOG_MAX_LINES]
    try:
        print(line, flush=True)
    except UnicodeEncodeError:  # cp1252 consoles on Windows
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)


# ── History access (single writer lock, read-modify-write per entry) ─────────
_history_lock = threading.Lock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def update_entry(url: str, **fields: Any) -> Dict[str, Any]:
    """Reload history, patch one entry, save. Never writes a stale snapshot."""
    with _history_lock:
        history = load_history()
        if url not in history:
            raise KeyError(url)
        entry = history[url]
        entry.update(fields)
        if "application_status" in fields:
            entry["application_status_label"] = STATUS_LABELS.get(
                fields["application_status"], fields["application_status"].replace("_", " ").title()
            )
            entry["status_updated_at"] = _now()
        save_history(history)
        return entry


def _doc_paths(entry: Dict[str, Any]) -> Dict[str, Optional[Path]]:
    """Resolve this entry's own CV / cover letter on disk (None if missing)."""
    folder = entry.get("folder_name") or ""
    out: Dict[str, Optional[Path]] = {"cv": None, "letter": None}
    if folder and "/" not in folder and "\\" not in folder and ".." not in folder:
        d = APPLICATIONS_DIR / folder
        if d.is_dir():
            # Match names case-insensitively so the result is the same on every OS
            # (older runs wrote 'anschreiben.pdf').
            by_name = {p.name.lower(): p for p in d.iterdir() if p.is_file()}
            out["cv"] = by_name.get("cv.pdf")
            out["letter"] = by_name.get("anschreiben.pdf")
    return out


def _doc_problem(entry: Dict[str, Any]) -> Optional[str]:
    """None if both documents exist and the letter is the typeset version."""
    docs = _doc_paths(entry)
    if docs["cv"] is None:
        return "CV.pdf is missing"
    return letter_problem(docs["letter"])


def _docs_on_disk(entry: Dict[str, Any]) -> bool:
    return _doc_problem(entry) is None


def _tier_rank(tier: Any) -> int:
    return tier if tier in (1, 2) else 9  # Tier 1 first, Tier 2 second, rest last


def _row(url: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    docs = _doc_paths(entry)
    folder = entry.get("folder_name") or ""
    return {
        "url": url,
        "company": entry.get("company") or "Unknown",
        "job_title": entry.get("job_title") or "Unknown",
        "location": entry.get("location") or "Unknown",
        "tier": entry.get("tier", 0),
        "tier_label": entry.get("tier_label", "Tier 0"),
        "match_score": entry.get("match_score", 0),
        "job_type": entry.get("job_type", ""),
        "status": entry.get("application_status", "unknown"),
        "status_label": entry.get("application_status_label", ""),
        "status_updated_at": entry.get("status_updated_at", ""),
        "note": entry.get("status_note") or entry.get("submission_note") or "",
        "blocker": entry.get("automation_blocker"),
        "apply_email": entry.get("apply_email"),
        "email_sent_at": entry.get("email_sent_at"),
        "docs_ready": _doc_problem(entry) is None,
        "doc_problem": _doc_problem(entry),
        "cv_url": f"/applications/{folder}/{docs['cv'].name}" if docs["cv"] else None,
        "letter_url": f"/applications/{folder}/{docs['letter'].name}" if docs["letter"] else None,
    }


def _email_sender() -> str:
    try:
        import mailer
        return mailer.configured_sender()
    except Exception:  # noqa: BLE001
        return ""


_send_lock = threading.Lock()


def send_email_application(url: str) -> Dict[str, Any]:
    """
    Send the job's Bewerbung.eml once. Marks the job 'submitted'.
    Refuses if it was already sent, if the draft is missing, or if the job is not
    an e-mail application.
    """
    import mailer

    with _send_lock:
        entry = load_history().get(url)
        if entry is None:
            raise KeyError(url)
        if entry.get("email_sent_at"):
            raise ValueError(f"already sent on {entry['email_sent_at']} to {entry.get('apply_email')}")
        if entry.get("automation_blocker") != "email_application" or not entry.get("apply_email"):
            raise ValueError("this job has no e-mail application draft")
        problem = _doc_problem(entry)
        if problem:
            raise ValueError(problem)
        docs = _doc_paths(entry)
        eml = APPLICATIONS_DIR / (entry.get("folder_name") or "") / "Bewerbung.eml"
        # attach the CURRENT files, not the copies stored in the draft
        sent = mailer.send_application(eml, expected_to=entry["apply_email"],
                                       attachments={"Anschreiben.pdf": docs["letter"], "Lebenslauf.pdf": docs["cv"]})
        update_entry(url, application_status="submitted", status_source="email", submitted_at=_now(),
                     email_sent_at=_now(), email_message_id=sent["message_id"],
                     submission_note=f"Application sent by e-mail to {sent['to']} from {sent['sent_from']}")
        return sent


def ready_email_urls() -> List[str]:
    """Jobs with a prepared e-mail application that has not been sent yet (Tier 1 first)."""
    rows = [
        (u, e) for u, e in load_history().items()
        if e.get("application_status") == "needs_manual" and e.get("automation_blocker") == "email_application"
        and e.get("apply_email") and not e.get("email_sent_at")
    ]
    rows.sort(key=lambda ue: (_tier_rank(ue[1].get("tier")), -(ue[1].get("match_score") or 0)))
    return [u for u, _ in rows]


_send_all_lock = threading.Lock()
SEND_ALL_STATE: Dict[str, Any] = {"running": False, "done": 0, "total": 0}


def run_send_all() -> Dict[str, int]:
    """Send every ready e-mail application, SEND_PAUSE_S apart. Safe to re-run: sent jobs are skipped."""
    import time as _t
    urls = ready_email_urls()
    SEND_ALL_STATE.update(running=True, done=0, total=len(urls))
    sent = failed = 0
    log(f"Send all: {len(urls)} prepared e-mail application(s) from {_email_sender()}")
    try:
        for i, url in enumerate(urls):
            if i:
                _t.sleep(SEND_PAUSE_S)
            entry = load_history().get(url, {})
            label = f"{entry.get('company', 'Unknown')} / {entry.get('job_title', 'Unknown')}"
            try:
                out = send_email_application(url)
                sent += 1
                log(f"  SENT {label} -> {out['to']}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                log(f"  NOT SENT {label}: {exc}")
            SEND_ALL_STATE["done"] = i + 1
    finally:
        SEND_ALL_STATE["running"] = False
    log(f"Send all complete: {sent} sent, {failed} not sent")
    return {"sent": sent, "failed": failed}


def build_state() -> Dict[str, Any]:
    history = load_history()
    targets = load_targets()

    rows = [_row(u, e) for u, e in history.items()]
    rows.sort(key=lambda r: (_tier_rank(r["tier"]), -(r["match_score"] or 0)))

    review_queue = [r for r in rows if r["status"] == "pending"]

    status_breakdown: Dict[str, int] = {}
    for r in rows:
        status_breakdown[r["status"]] = status_breakdown.get(r["status"], 0) + 1

    metrics = {
        "total_discovered": len(targets),
        "total_applications": len(rows),
        "docs_ready": sum(1 for r in rows if r["docs_ready"]),
        "docs_broken": sum(1 for r in rows if r["doc_problem"] and "fallback" in r["doc_problem"]),
        "docs_missing": sum(1 for r in rows if not r["docs_ready"]),
        "pending_review": sum(1 for r in review_queue if r["docs_ready"]),
        "approved": status_breakdown.get("approved", 0),
        "submitted": status_breakdown.get("submitted", 0),
        "tier1_count": sum(1 for r in rows if r["tier"] == 1),
        "tier2_count": sum(1 for r in rows if r["tier"] == 2),
        "status_breakdown": status_breakdown,
    }
    return {
        "metrics": metrics,
        "review_queue": review_queue,
        "applications": rows,
        "status_labels": STATUS_LABELS,
        "busy": _current_task,
        "settings": dict(SETTINGS),
        "email_sender": _email_sender(),
        "send_all": dict(SEND_ALL_STATE),
        "attention": ATTENTION,
        "log": LOG_BUFFER[-100:],
    }


# ── Background task runner: one pipeline task at a time ──────────────────────
_task_lock = threading.Lock()
_current_task: Optional[str] = None


def _start_task(name: str, fn: Callable[[], None], background_tasks: BackgroundTasks) -> Dict[str, Any]:
    global _current_task
    if not _task_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=f"'{_current_task}' is still running. Wait for it to finish.")
    _current_task = name

    def _runner() -> None:
        global _current_task
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            log(f"{name} error: {exc}")
            traceback.print_exc()
        finally:
            _current_task = None
            _task_lock.release()

    background_tasks.add_task(_runner)
    return {"status": "started", "message": f"{name} started in background"}


def _run_script(label: str, args: List[str], timeout_s: int) -> int:
    """Run a pipeline script as a subprocess and stream its output into the log.

    A fresh process always runs the code currently on disk and keeps Playwright
    out of the server's threads.
    """
    log(f"{label} started...")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        [sys.executable, *args],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        cwd=str(_JOB_DIR), env=env,
    )
    timer = threading.Timer(timeout_s, proc.kill)
    timer.start()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if line:
                log(f"  {line}")
        code = proc.wait()
    finally:
        timer.cancel()
    log(f"{label} FAILED (exit code {code})" if code != 0 else f"{label} complete.")
    return code


def _run_discover(max_jobs: int = 50) -> None:
    _run_script("Job discovery", [str(_JOB_DIR / "job_scraper.py"), "--max-jobs", str(max_jobs)], DISCOVER_TIMEOUT_S)


def _run_generate() -> None:
    _run_script("Document generation", [str(_JOB_DIR / "batch_processor.py"), "--no-discover"], GENERATE_TIMEOUT_S)


def _run_execute() -> None:
    try:
        from apply_browser_agent import apply_to_job
    except SystemExit:
        log("Execute aborted: playwright is not installed in this Python environment.")
        return

    history = load_history()
    approved = [
        (u, e) for u, e in history.items() if e.get("application_status") == "approved"
    ]
    approved.sort(key=lambda ue: (_tier_rank(ue[1].get("tier")), -(ue[1].get("match_score") or 0)))
    if not approved:
        log("Execute: nothing to do. Approve jobs in the Review tab first.")
        return

    cfg = dict(SETTINGS)   # one consistent set of switches for the whole run
    can_send = cfg["auto_send_email"] and bool(_email_sender())
    if cfg["auto_send_email"] and not can_send:
        log("Execute: auto-send e-mail is ON but Gmail is not set up in Job/.env - drafts will be kept, not sent")
    last_send = 0.0
    log(f"Execute: {len(approved)} approved application(s); auto-submit {'ON' if cfg['auto_submit'] else 'OFF'}, "
        f"assist {'ON' if cfg['assist'] else 'OFF'}, privacy consent {'ON' if cfg['accept_privacy'] else 'OFF'}")
    counts = {"submitted": 0, "awaiting_approval": 0, "needs_manual": 0, "dropped": 0, "failed": 0}

    def on_event(msg: str) -> None:
        global ATTENTION
        if msg.startswith("ACTION NEEDED") or msg.startswith("Form is ready"):
            ATTENTION = msg
        else:
            ATTENTION = None
        log(f"  >> {msg}")
    for url, entry in approved:
        label = f"{entry.get('company', 'Unknown')} / {entry.get('job_title', 'Unknown')}"
        docs = _doc_paths(entry)
        problem = _doc_problem(entry)
        if problem:
            update_entry(url, application_status="failed", status_source="browser_agent",
                         submission_note=f"Not attempted: {problem}")
            counts["failed"] += 1
            log(f"  FAILED {label}: {problem}")
            continue
        try:
            log(f"  -> {label}")
            result = apply_to_job(url=url, headless=not cfg["assist"], auto_approve=cfg["auto_submit"],
                                  assist=cfg["assist"], accept_privacy=cfg["accept_privacy"], on_event=on_event,
                                  cv_path=docs["cv"], letter_path=docs["letter"])
        except Exception as exc:  # noqa: BLE001
            result = {"status": "error", "note": str(exc)}
        finally:
            globals()["ATTENTION"] = None
        status = result.get("status")
        new_status = status if status in ("submitted", "awaiting_approval", "needs_manual") else "failed"
        if result.get("blocker") == "posting_unavailable":
            new_status = "dropped"      # dead link: nothing a person can do either
        fields: Dict[str, Any] = {
            "application_status": new_status,
            "status_source": "browser_agent",
            "submission_note": result.get("note", ""),
            "automation_blocker": result.get("blocker"),
            "apply_email": result.get("apply_email"),
        }
        if new_status == "submitted":
            fields["submitted_at"] = _now()
        update_entry(url, **fields)
        if can_send and result.get("blocker") == "email_application" and result.get("apply_email"):
            import time as _t
            wait = SEND_PAUSE_S - (_t.monotonic() - last_send)
            if wait > 0:
                _t.sleep(wait)
            try:
                sent = send_email_application(url)
                last_send = _t.monotonic()
                new_status = "submitted"
                result["note"] = f"sent by e-mail to {sent['to']}"
                result["blocker"] = None
            except Exception as exc:  # noqa: BLE001
                log(f"  E-mail NOT sent for {label}: {exc}")
        counts[new_status] += 1
        blocker = f" [{result['blocker']}]" if result.get("blocker") else ""
        log(f"  {new_status.upper()}{blocker} {label}: {result.get('note', '')}")

    log(f"Execute complete: {counts['submitted']} submitted, {counts['awaiting_approval']} filled but not submitted, "
        f"{counts['needs_manual']} need you, {counts['dropped']} dead postings dropped, {counts['failed']} failed")


def _run_auto() -> None:
    for cycle in range(1, AUTO_CYCLES + 1):
        log(f"--- Auto cycle {cycle}/{AUTO_CYCLES}: discover, generate, execute approved ---")
        _run_discover(max_jobs=20)
        _run_generate()
        _run_execute()  # only jobs you approved; new ones wait in the Review tab
    log("=== Auto cycle complete ===")


# ── Routes ────────────────────────────────────────────────────────────────────
class DecisionBody(BaseModel):
    urls: List[str]
    decision: str


class UrlBody(BaseModel):
    url: str


class StatusBody(BaseModel):
    url: str
    status: str
    note: str = ""


class BatchActionRequest(BaseModel):
    ids: List[str]


@app.get("/", response_class=HTMLResponse)
def root() -> HTMLResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="UI not found")
    return HTMLResponse(content=index_path.read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-store"})


@app.get("/api/state")
def get_state() -> Dict[str, Any]:
    return build_state()


@app.post("/api/discover")
def discover(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    return _start_task("Job discovery", _run_discover, background_tasks)


@app.post("/api/generate")
def generate_docs(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    return _start_task("Document generation", _run_generate, background_tasks)


@app.post("/api/execute")
def execute_approved(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    return _start_task("Execute approved", _run_execute, background_tasks)


@app.post("/api/auto-cycle")
def auto_cycle(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    return _start_task("Auto cycle", _run_auto, background_tasks)


@app.post("/api/decision")
def decide(body: DecisionBody) -> Dict[str, Any]:
    """Approve or reject one or more jobs waiting in the review queue."""
    if body.decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision must be 'approve' or 'reject'")
    history = load_history()
    done: List[str] = []
    errors: List[str] = []
    for url in body.urls:
        entry = history.get(url)
        if entry is None:
            errors.append(f"not found: {url}")
            continue
        name = f"{entry.get('company', 'Unknown')} / {entry.get('job_title', 'Unknown')}"
        if body.decision == "approve":
            if not _docs_on_disk(entry):
                errors.append(f"{name}: {_doc_problem(entry)}, cannot approve")
                continue
            update_entry(url, application_status="approved", status_source="human_approve")
            log(f"Approved: {name}")
        else:
            update_entry(url, application_status="skipped", status_source="human_reject")
            log(f"Rejected: {name}")
        done.append(url)
    if not done and errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    return {"decision": body.decision, "updated": done, "errors": errors}


@app.post("/api/approve-batch")
def approve_batch(body: BatchActionRequest) -> Dict[str, Any]:
    """Approve multiple jobs in batch. Skips entries in protected statuses."""
    history = load_history()
    done: List[str] = []
    skipped: List[str] = []
    errors: List[str] = []
    for url in body.ids:
        entry = history.get(url)
        if entry is None:
            errors.append(f"not found: {url}")
            continue
        current_status = entry.get("application_status", "")
        if current_status in PROTECTED_STATUSES:
            skipped.append(f"{url} (already {current_status})")
            continue
        name = f"{entry.get('company', 'Unknown')} / {entry.get('job_title', 'Unknown')}"
        if not _docs_on_disk(entry):
            errors.append(f"{name}: {_doc_problem(entry)}, cannot approve")
            continue
        try:
            update_entry(url, application_status="approved", status_source="human_approve_batch")
            log(f"Batch approved: {name}")
            done.append(url)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    return {"action": "approve", "updated": done, "skipped": skipped, "errors": errors}


@app.post("/api/reject-batch")
def reject_batch(body: BatchActionRequest) -> Dict[str, Any]:
    """Reject (skip) multiple jobs in batch. Skips entries in protected statuses."""
    history = load_history()
    done: List[str] = []
    skipped: List[str] = []
    errors: List[str] = []
    for url in body.ids:
        entry = history.get(url)
        if entry is None:
            errors.append(f"not found: {url}")
            continue
        current_status = entry.get("application_status", "")
        if current_status in PROTECTED_STATUSES:
            skipped.append(f"{url} (already {current_status})")
            continue
        name = f"{entry.get('company', 'Unknown')} / {entry.get('job_title', 'Unknown')}"
        try:
            update_entry(url, application_status="skipped", status_source="human_reject_batch")
            log(f"Batch rejected: {name}")
            done.append(url)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    return {"action": "reject", "updated": done, "skipped": skipped, "errors": errors}


class SettingsBody(BaseModel):
    auto_submit: Optional[bool] = None
    auto_send_email: Optional[bool] = None
    assist: Optional[bool] = None
    accept_privacy: Optional[bool] = None


@app.post("/api/send-email")
def send_email(body: UrlBody) -> Dict[str, Any]:
    """Send one prepared e-mail application now (button in the Tracker)."""
    try:
        sent = send_email_application(body.url)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found in history")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"E-mail not sent: {exc}")
    log(f"Sent by e-mail to {sent['to']} from {sent['sent_from']}")
    return {"status": "submitted", **sent}


@app.post("/api/send-email-all")
def send_email_all(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """Send all prepared, unsent e-mail applications in the background (works while Execute runs)."""
    if not _email_sender():
        raise HTTPException(status_code=400, detail="Gmail is not set up in Job/.env")
    count = len(ready_email_urls())
    if not count:
        raise HTTPException(status_code=400, detail="No prepared e-mail applications left to send")
    if not _send_all_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Sending is already in progress")

    def _runner() -> None:
        try:
            run_send_all()
        except Exception as exc:  # noqa: BLE001
            log(f"Send all error: {exc}")
        finally:
            _send_all_lock.release()

    background_tasks.add_task(_runner)
    return {"status": "started", "count": count,
            "message": f"Sending {count} e-mail application(s), {SEND_PAUSE_S} s apart"}


@app.post("/api/settings")
def set_settings(body: SettingsBody) -> Dict[str, Any]:
    """Change the Execute switches from the dashboard (persisted in settings.json)."""
    changed = {k: v for k, v in body.model_dump().items() if v is not None}
    SETTINGS.update(changed)
    SETTINGS_PATH.write_text(json.dumps(SETTINGS, indent=2), encoding="utf-8")
    for key, value in changed.items():
        log(f"Setting changed: {key} = {'ON' if value else 'OFF'}")
    return {"settings": dict(SETTINGS)}


@app.post("/api/status")
def set_status(body: StatusBody) -> Dict[str, Any]:
    """Manually set an application's tracker status (and optional note)."""
    if body.status not in STATUS_LABELS:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(STATUS_LABELS)}")
    fields: Dict[str, Any] = {"application_status": body.status, "status_source": "manual"}
    if body.note:
        fields["status_note"] = body.note
    try:
        entry = update_entry(body.url, **fields)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found in history")
    log(f"Status updated: {entry.get('company', 'Unknown')} / {entry.get('job_title', 'Unknown')} -> {body.status}")
    return {"status": body.status, "url": body.url}


def main() -> None:
    parser = argparse.ArgumentParser(description="Job Applier Control Center")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7788)
    args = parser.parse_args()
    log(f"Job Applier Control Center starting on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
