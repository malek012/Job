#!/usr/bin/env python3
"""
selftest.py - end-to-end self-test for the Job Applier pipeline.

    python selftest.py            run everything
    python selftest.py --no-browser   skip the browser-agent form tests

Safe to run at any time: it copies the code into a temporary folder with
synthetic data and works there. It never touches the real history.json,
targets.json or _applications/, never contacts a job site and never submits
anything (the browser tests use local pages served from 127.0.0.1).

Exit code 0 = no FAIL. SKIP means a prerequisite is missing on this machine.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import py_compile
import re
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List

HERE = Path(__file__).resolve().parent
# Unsafe click patterns that must never come back into the agent (they can press a
# submit button on a page that was never recognised as an application form).
FORBIDDEN_IN_AGENT = [r"\[class\*='btn' i\]", r'"senden", "submit"', r"\.forEach\(el => \{[^}]*el\.click\(\)"]

CORE = ["doc_quality.py", "relevance.py", "job_analyzer.py", "job_scraper.py", "batch_processor.py",
        "apply_browser_agent.py", "api_server.py", "cleanup_data.py", "mailer.py", "selftest.py"]
RESULTS: List[tuple] = []


class Skip(Exception):
    pass


def check(name: str, fn: Callable[[], Any]) -> None:
    try:
        detail = fn()
        RESULTS.append(("PASS", name, str(detail or "")))
    except Skip as exc:
        RESULTS.append(("SKIP", name, str(exc)))
    except AssertionError as exc:
        RESULTS.append(("FAIL", name, str(exc) or "assertion failed"))
    except Exception as exc:  # noqa: BLE001
        RESULTS.append(("FAIL", name, f"{type(exc).__name__}: {str(exc)[:300]}"))


# ───────────────────────── outer stage: build the sandbox ─────────────────────────

SANDBOX_TARGETS = [
    {"url": "https://example.invalid/jobs/1", "company": "Alpha Robotics GmbH",
     "job_title": "Werkstudent Softwareentwicklung Python (m/w/d)", "location": "Dortmund", "source": "selftest"},
    {"url": "https://example.invalid/jobs/2", "company": "Beta Vision AG",
     "job_title": "Computer Vision Engineer (m/w/d)", "location": "München", "source": "selftest"},
    {"url": "https://example.invalid/jobs/3", "company": "Gamma Sales GmbH",
     "job_title": "Account Executive DACH", "location": "Köln", "source": "selftest"},
    {"url": "https://example.invalid/jobs/4", "company": "Delta Soft",
     "job_title": "Senior Software Engineer", "location": "Essen", "source": "selftest"},
    {"url": "https://example.invalid/jobs/5", "company": "Epsilon Ltd",
     "job_title": "Embedded Software Engineer", "location": "London", "source": "selftest"},
]


def build_sandbox() -> Path:
    root = Path(tempfile.mkdtemp(prefix="jobapplier_selftest_"))
    job = root / "Job"
    job.mkdir()
    for name in CORE:
        shutil.copy2(HERE / name, job / name)
    for folder in ("static", "cover_applier", "cv_applier"):
        shutil.copytree(HERE / folder, job / folder, ignore=shutil.ignore_patterns("__pycache__", "*.aux", "*.log", "*.out"))
    for name in ("pdf_writer.py", "profile.json"):   # pdf_writer: imported by the cover-letter builder
        shutil.copy2(HERE / name, job / name)
    (job / "targets.json").write_text(json.dumps(SANDBOX_TARGETS, ensure_ascii=False, indent=2), encoding="utf-8")
    (job / "history.json").write_text("{}", encoding="utf-8")
    (job / "_applications").mkdir()
    return root


# ───────────────────────── inner stage: the checks ─────────────────────────

def t_compile() -> str:
    for name in CORE:
        py_compile.compile(str(HERE / name), doraise=True)
    return f"{len(CORE)} modules"


def t_agent_safety() -> str:
    src = (HERE / "apply_browser_agent.py").read_text(encoding="utf-8")
    for pattern in FORBIDDEN_IN_AGENT:
        assert not re.search(pattern, src), f"unsafe click pattern in apply_browser_agent.py: {pattern}"
    for needle in ("def human_blocker", "def wait_for_human", "def required_unfilled", "def verify_submission"):
        assert needle in src, f"safety gate missing: {needle}"
    assert "set_input_files" in src and ".fill(" in src
    return "no blind-click fallbacks, all gates present"


def t_imports() -> str:
    import importlib
    for mod in ("relevance", "job_analyzer", "job_scraper", "batch_processor", "cleanup_data", "api_server"):
        importlib.import_module(mod)
    return "all pipeline modules import (apply_browser_agent is checked with the browser tests)"


def t_relevance() -> str:
    from relevance import is_relevant_title as r
    yes = ["Werkstudent C# Softwareentwicklung (m/w/d), Dortmund", "Werkstudent KI (m|w|d)", "AIT Test Engineer",
           "Embedded Softwareentwickler (m/w/d)", "Applied AI Engineer / Machine Learning Engineer (m/w/d)",
           "Werkstudent Computer Vision", "Vollzeit C++ Robotics ROS2", "Junior Softwareentwickler",
           "Werkstudent*in (m/w/d) Erforschung KI-Anwendungen", "Engineer* Embedded Software"]
    no = ["Account Executive DACH", "Customer Success Manager (m/w/d)", "Solutions Engineer - Italian",
          "Technical Customer Success Manager (AI Manufacturing Enterprise)", "Werkstudent (m/w/d)",
          "Werkstudent*in Account- & Produktmanagement (m/w/d)", "Werkstudent:in im Arbeitgebermarketing (m/w/d)",
          "Senior Finance Manager", "Lagerarbeiter (m/w/d) - Teilzeit", "Staff Forward Deployed Engineer, EMEA", ""]
    wrong = [t for t in yes if not r(t)[0]] + [t for t in no if r(t)[0]]
    assert not wrong, f"misclassified: {wrong}"
    return f"{len(yes)} technical kept, {len(no)} non-technical dropped"


BA_V6 = {"maxErgebnisse": 4, "page": 1, "size": 50, "ergebnisliste": [
    {"stellenangebotsart": "ARBEIT", "stellenangebotsTitel": "Werkstudent C# Softwareentwicklung (m/w/d), Dortmund",
     "firma": "Materna Information & Communications SE", "referenznummer": "12811-2358267-S",
     "externeURL": "https://www.finest-jobs.com/stellenanzeige/x-1326743?cp=BA",
     "stellenlokationen": [{"adresse": {"plz": "44263", "ort": "Dortmund", "region": "NORDRHEIN_WESTFALEN"}}]},
    {"stellenangebotsart": "PRAKTIKUM_TRAINEE", "stellenangebotsTitel": "Werkstudent KI (m|w|d)", "firma": "VDM Metals GmbH",
     "referenznummer": "19041-44549200-113-S", "stellenlokationen": [{"adresse": {"ort": "Dortmund"}}]},
    {"stellenangebotsart": "SELBSTAENDIGKEIT", "stellenangebotsTitel": "Machine Learning", "firma": "alfatraining",
     "referenznummer": "10000-1183227598-S", "stellenlokationen": [{"adresse": {"ort": "Bochum"}}]},
    {"stellenangebotsart": "PRAKTIKUM_TRAINEE", "stellenangebotsTitel": "Werkstudent:in im Arbeitgebermarketing (m/w/d)",
     "firma": "Stadt Hamm", "referenznummer": "10000-1208581485-S", "stellenlokationen": [{"adresse": {"ort": "Hamm, Westfalen"}}]},
]}
BA_V4 = {"stellenangebote": [{"titel": "Softwareentwickler (m/w/d)", "arbeitgeber": "X GmbH", "refnr": "10000-1-S",
                              "arbeitsort": {"ort": "Essen"}}]}


def t_ba_parser() -> str:
    import job_scraper as js
    v6 = js.parse_ba_response(BA_V6)
    assert len(v6) == 3, f"expected 3 usable v6 rows (training course dropped), got {len(v6)}"
    first = v6[0]
    assert set(first) >= {"url", "company", "job_title", "location", "source"}, first
    assert first["url"].startswith("https://www.finest-jobs.com/"), first["url"]
    assert first["company"].startswith("Materna") and first["location"] == "Dortmund"
    assert v6[1]["url"] == "https://www.arbeitsagentur.de/jobsuche/jobdetail/19041-44549200-113-S", v6[1]["url"]
    v4 = js.parse_ba_response(BA_V4)
    assert len(v4) == 1 and v4[0]["job_title"].startswith("Softwareentwickler") and v4[0]["location"] == "Essen"
    assert js.parse_ba_response({}) == [] and js.parse_ba_response(None) == []
    kept = js._technical_only(v6, "selftest")
    assert [j["company"] for j in kept] == ["Materna Information & Communications SE", "VDM Metals GmbH"], kept
    return "v6 + v4 schemas parsed; course and marketing rows dropped"


def t_ba_fetch_mocked() -> str:
    """fetch_ba_jobs end to end with the HTTP layer replaced: v4 fails, v6 answers."""
    import job_scraper as js

    class Resp:
        def __init__(self, code: int, data: Any) -> None:
            self.status_code, self._d, self.text = code, data, json.dumps(data)[:200]

        def json(self) -> Any:
            return self._d

    calls: List[str] = []

    def fake_get(url: str, **kw: Any) -> Resp:
        calls.append(url)
        return Resp(200, BA_V6) if "/v6/" in url else Resp(404, {"error": "gone"})

    real = js.requests.get
    js.requests.get = fake_get
    try:
        jobs = js.fetch_ba_jobs(max_jobs=10)
    finally:
        js.requests.get = real
    assert len(jobs) == 2, f"expected 2 unique technical jobs across all queries, got {len(jobs)}"
    assert all("/v6/" in c for c in calls), "v6 must be tried first"
    assert all(k in jobs[0] for k in ("url", "company", "job_title", "location", "source"))
    return f"{len(calls)} queries -> {len(jobs)} deduplicated technical jobs"


def t_scoring() -> str:
    import batch_processor as bp
    prof = bp._load_profile()
    res = {j["url"][-1]: bp.analyze_and_score_job(j, prof) for j in SANDBOX_TARGETS}
    assert res["1"]["in_scope"] and res["1"]["tier"] == 1 and res["1"]["job_type"] == "werkstudent", res["1"]
    assert res["2"]["in_scope"] and res["2"]["tier"] == 2, res["2"]
    assert not res["3"]["in_scope"] and not res["3"]["relevant"], "sales role must be dropped by the domain gate"
    assert not res["4"]["in_scope"], "senior role must be out of scope"
    assert not res["5"]["in_scope"] and res["5"]["tier"] == 0, "role outside Germany must be out of scope"
    order = [j["_analysis"]["tier"] for j in bp.score_and_sort_targets(SANDBOX_TARGETS, prof)]
    ranked = [t if t in (1, 2) else 9 for t in order]
    assert ranked == sorted(ranked), f"Tier 1 must sort first: {order}"
    a = bp.analyze_and_score_job({"url": "https://x/k", "job_title": "Softwarearchitekt Kotlin", "location": "",
                                  "company": "R", "job_description": "Standort Koln"}, prof)
    assert a["tier"] == 1, "ASCII spelling 'Koln' must count as Tier 1"
    b = bp.analyze_and_score_job({"url": "https://x/i", "job_title": "Softwareentwickler", "location": "Wien",
                                  "company": "R", "job_description": "Ihre Interessen: gmail"}, prof)
    assert b["tier"] == 0 and "ai" not in b["tech_stack"], "'Interessen'/'gmail' must not match essen/ai"
    return "scope, domain gate, tier order, whole-word matching"


def _run_generator() -> str:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", JOB_APPLIER_PAUSE_SECONDS="0")
    proc = subprocess.run([sys.executable, str(HERE / "batch_processor.py"), "--no-discover"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          cwd=str(HERE), env=env, timeout=600)
    assert proc.returncode == 0, f"batch_processor exit {proc.returncode}: {(proc.stdout + proc.stderr)[-400:]}"
    return proc.stdout


def _find(folder: Path, name: str) -> Path:
    for p in folder.iterdir():
        if p.name.lower() == name.lower():
            return p
    raise AssertionError(f"{name} missing in {folder.name}")


def t_generate() -> str:
    out = _run_generator()
    hist = json.loads((HERE / "history.json").read_text(encoding="utf-8"))
    urls = sorted(u[-1] for u in hist)
    assert urls == ["1", "2"], f"documents expected for jobs 1 and 2 only, history has {urls}\n{out[-400:]}"
    for url, e in hist.items():
        for key in ("application_status", "company", "job_title", "location", "tier", "tier_label", "match_score",
                    "job_type", "docs_ready", "folder_name"):
            assert key in e, f"history entry lacks '{key}'"
        assert e["application_status"] == "pending" and e["docs_ready"] is True, e
        folder = HERE / "_applications" / e["folder_name"]
        cv, letter = _find(folder, "CV.pdf"), _find(folder, "Anschreiben.pdf")
        assert cv.stat().st_size > 1000 and letter.stat().st_size > 1000, "PDF looks empty"
        assert letter.name == "Anschreiben.pdf", f"cover letter is named {letter.name}"
        assert letter.read_bytes()[:5] == b"%PDF-", "cover letter is not a PDF"
    assert "non-technical" in out, "generator did not report the domain gate"
    return "2 of 5 targets generated (sales, senior and non-German roles skipped)"


def t_letter_escaping() -> str:
    """Company names / titles with LaTeX special characters must give a typeset letter, never a fallback."""
    sys.path.insert(0, str(HERE / "cover_applier"))
    import build_cover_letter as bcl
    from doc_quality import letter_problem
    tricky = "Röhrich & Partner, C# / .NET, 100% Remote_Team {x} ~ ^ \\ $"
    esc = bcl._latex_escape_text(tricky)
    assert "textbackslash{}&" not in esc and "textbackslash{}#" not in esc, f"double escaping: {esc}"
    assert esc.count("\\textbackslash{}") == 1, esc
    work = Path(tempfile.mkdtemp(prefix="letter_"))
    payload = {"company_name": "Röhrich & Partner GmbH", "company_address": "", "job_title": "Softwareentwickler C# & .NET (m/w/d)",
               "salutation": "Sehr geehrte Damen und Herren,", "paragraph_1": "Test 100% & #1.", "paragraph_2": "Zweiter Absatz.",
               "paragraph_3": "Dritter Absatz."}
    (work / "p.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if shutil.which("pdflatex") is None:
        raise Skip("pdflatex not installed here")
    bcl.build_cover_letter(work / "p.json", HERE / "cover_applier" / "anschreiben_template.tex", work / "Anschreiben.tex", work / "Anschreiben.pdf")
    assert letter_problem(work / "Anschreiben.pdf") is None, letter_problem(work / "Anschreiben.pdf")
    # a LaTeX failure must raise and leave no PDF behind (no silent plain-text fallback)
    (work / "bad.tex").write_text("\\documentclass{article}\\begin{document}\\undefinedmacro\\end{document}", encoding="utf-8")
    (work / "bad.pdf").write_bytes(b"%PDF-1.4 stale")
    os.environ.pop("JOB_APPLIER_ALLOW_PLAIN_LETTER", None)
    try:
        bcl.compile_pdf(work / "bad.tex", work / "bad.pdf")
        raise AssertionError("a failed LaTeX compile did not raise")
    except RuntimeError:
        pass
    assert not (work / "bad.pdf").exists(), "a stale PDF survived a failed compile"
    tiny = work / "plain.pdf"
    tiny.write_bytes(b"%PDF-1.4\n" + b"x" * 2000)
    assert letter_problem(tiny), "plain-text fallback letter not detected"
    shutil.rmtree(work, ignore_errors=True)
    return "'&', '#', '%', '_' typeset correctly; LaTeX failures raise; fallback letters are detected"


def t_letter_text() -> str:
    hist = json.loads((HERE / "history.json").read_text(encoding="utf-8"))
    checked = 0
    for e in hist.values():
        folder = HERE / "_applications" / e["folder_name"]
        tex = _find(folder, "Anschreiben.tex").read_text(encoding="utf-8", errors="replace")
        payload = _find(folder, "cover_letter_payload.json").read_text(encoding="utf-8")
        for label, text in (("letter .tex", tex), ("payload", payload)):
            assert not re.search(r"master", text, re.I), f"{label} mentions a Master's degree"
        assert "B.Sc." in tex and "TU Dortmund" in tex, "completed B.Sc. at TU Dortmund not stated"
        assert e["company"] in payload, "company name missing from letter payload"
        checked += 1
    assert checked, "no generated letters to inspect"
    template = (HERE / "cover_applier" / "anschreiben_template.tex").read_text(encoding="utf-8")
    assert not re.search(r"master", template, re.I), "letter template mentions a Master's degree"
    return f"{checked} letters: no Master mention, B.Sc. TU Dortmund present"


def t_regenerate() -> str:
    hist_path = HERE / "history.json"
    hist = json.loads(hist_path.read_text(encoding="utf-8"))
    victim = next(u for u in hist if u.endswith("/1"))
    del hist[victim]
    hist_path.write_text(json.dumps(hist), encoding="utf-8")
    _run_generator()
    hist = json.loads(hist_path.read_text(encoding="utf-8"))
    assert victim in hist and hist[victim]["docs_ready"], "removed history entry was not regenerated"
    out = _run_generator()
    assert len(json.loads(hist_path.read_text(encoding="utf-8"))) == 2, "second run must not duplicate entries"
    return "entry removed from history is rebuilt; re-running adds nothing"


def t_cleanup() -> str:
    import cleanup_data as cd
    assert cd.test_reason("https://www.stepstone.de/stellenangebote--X--Y--13900004-inline.html", "RWE", "Dev")
    assert cd.test_reason("https://www.stellenwerk.de/account/book/job-ad", "Unknown", "In 5 Schritten")
    assert cd.test_reason("https://www.stepstone.de/stellenangebote--X--Y--13786847-inline.html", "A", "Dev") is None
    plan = cd.plan(reset_missing=True)
    assert not plan["drop_history"], f"entries with documents must be kept: {plan['drop_history']}"
    assert not plan["drop_targets"], plan["drop_targets"]
    return "test-record rules; entries with PDFs are not reset"


STATE_KEYS = {"metrics", "review_queue", "applications", "status_labels", "busy", "settings", "attention", "email_sender", "send_all", "log"}
METRIC_KEYS = {"total_discovered", "total_applications", "docs_ready", "docs_broken", "docs_missing", "pending_review", "approved",
               "submitted", "tier1_count", "tier2_count", "status_breakdown"}
SETTING_KEYS = {"auto_submit", "assist", "accept_privacy", "auto_send_email"}
ROW_KEYS = {"url", "company", "job_title", "location", "tier", "tier_label", "match_score", "job_type", "status",
            "status_label", "status_updated_at", "note", "blocker", "apply_email", "email_sent_at", "docs_ready", "doc_problem", "cv_url", "letter_url"}


def t_api() -> str:
    import api_server as srv
    from fastapi import HTTPException
    st = srv.build_state()
    assert set(st) == STATE_KEYS, f"state keys {sorted(st)}"
    assert set(st["metrics"]) == METRIC_KEYS, f"metric keys {sorted(st['metrics'])}"
    assert st["metrics"]["total_discovered"] == 5 and st["metrics"]["docs_ready"] == 2 and st["metrics"]["pending_review"] == 2
    assert len(st["review_queue"]) == 2 and set(st["review_queue"][0]) == ROW_KEYS
    assert st["review_queue"][0]["tier"] == 1, "Tier 1 must be first in the review queue"
    row = st["review_queue"][0]
    served = HERE / "_applications" / row["cv_url"].split("/applications/")[1]
    assert served.is_file(), f"cv_url does not resolve to a file: {row['cv_url']}"
    json.dumps(st)  # must be serialisable

    u1, u2 = (r["url"] for r in st["review_queue"])
    res = srv.decide(srv.DecisionBody(urls=[u1], decision="approve"))
    assert res["updated"] == [u1]
    srv.decide(srv.DecisionBody(urls=[u2], decision="reject"))
    st = srv.build_state()
    assert st["metrics"]["status_breakdown"] == {"approved": 1, "skipped": 1}, st["metrics"]["status_breakdown"]
    assert st["metrics"]["approved"] == 1 and not st["review_queue"]

    # approval must be refused when the PDFs are gone
    hist = srv.load_history()
    folder = HERE / "_applications" / hist[u2]["folder_name"]
    hidden = folder.with_name(folder.name + "_hidden")
    folder.rename(hidden)
    try:
        srv.update_entry(u2, application_status="pending")
        try:
            srv.decide(srv.DecisionBody(urls=[u2], decision="approve"))
            raise AssertionError("approve succeeded although the PDFs are missing")
        except HTTPException as exc:
            assert exc.status_code == 400
        assert srv.build_state()["metrics"]["docs_missing"] == 1
    finally:
        hidden.rename(folder)

    for bad in (lambda: srv.decide(srv.DecisionBody(urls=[u1], decision="maybe")),
                lambda: srv.set_status(srv.StatusBody(url=u1, status="bogus")),
                lambda: srv.set_status(srv.StatusBody(url="https://nope", status="interview"))):
        try:
            bad()
            raise AssertionError("invalid request was accepted")
        except HTTPException:
            pass
    srv.set_status(srv.StatusBody(url=u2, status="interview", note="call"))
    assert srv.load_history()[u2]["application_status"] == "interview"
    for status in ("needs_manual", "awaiting_approval", "submitted", "approved", "skipped", "failed"):
        assert status in srv.STATUS_LABELS, f"no label for status '{status}'"
    assert set(st["settings"]) == SETTING_KEYS and not any(st["settings"].values()), "all Execute switches must default to OFF"
    assert srv.set_settings(srv.SettingsBody(assist=True))["settings"] == {"auto_submit": False, "assist": True, "accept_privacy": False, "auto_send_email": False}
    assert json.loads((HERE / "settings.json").read_text(encoding="utf-8"))["assist"] is True, "settings are not persisted"
    srv.set_settings(srv.SettingsBody(assist=False))
    paths = {r.path for r in srv.app.routes}
    for p in ("/", "/api/state", "/api/discover", "/api/generate", "/api/execute", "/api/auto-cycle",
              "/api/decision", "/api/status", "/api/settings", "/api/send-email", "/api/send-email-all"):
        assert p in paths, f"route {p} missing"
    return "state schema, approve/reject, missing-PDF gate, validation, routes"


def t_execute_gate() -> str:
    """Execute must only pick 'approved' jobs and must not submit without auto-submit."""
    import api_server as srv
    import types
    seen: List[Dict[str, Any]] = []
    fake = types.ModuleType("apply_browser_agent")

    def apply_to_job(**kw: Any) -> Dict[str, Any]:
        seen.append(kw)
        return {"status": "needs_manual", "blocker": "no_form", "note": "Complex form detected - manual intervention required: test"}

    fake.apply_to_job = apply_to_job
    real = sys.modules.get("apply_browser_agent")
    sys.modules["apply_browser_agent"] = fake
    try:
        srv._run_execute()
    finally:
        if real is not None:
            sys.modules["apply_browser_agent"] = real
        else:
            del sys.modules["apply_browser_agent"]
    hist = srv.load_history()
    approved_before = [u for u, e in hist.items() if e.get("status_source") == "browser_agent"]
    assert len(seen) == 1, f"Execute called the agent {len(seen)} times; exactly 1 job was approved"
    assert seen[0]["auto_approve"] is False, "auto-submit must be off by default"
    assert seen[0]["assist"] is False and seen[0]["accept_privacy"] is False, "assist / privacy consent must be off by default"
    assert Path(seen[0]["cv_path"]).parent.name == hist[seen[0]["url"]]["folder_name"], "agent got another job's CV"
    e = hist[seen[0]["url"]]
    assert e["application_status"] == "needs_manual" and e["automation_blocker"] == "no_form", e
    assert len(approved_before) == 1
    return "only the approved job was executed, with its own documents, auto-submit off"


class FakeSMTP:
    """Records what would have been sent; never opens a connection."""
    sent: List[Any] = []
    logins: List[tuple] = []

    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *a: Any) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        FakeSMTP.logins.append((user, password))

    def send_message(self, msg: Any) -> None:
        FakeSMTP.sent.append(msg)


def t_email() -> str:
    """Draft -> mailer -> API: sent once, from the configured account, with both PDFs; never twice."""
    import api_server as srv
    import mailer
    from fastapi import HTTPException
    from email import policy
    from email.parser import BytesParser

    for k in ("JOB_APPLIER_SMTP_USER", "JOB_APPLIER_SMTP_PASSWORD"):
        os.environ.pop(k, None)
    assert not mailer.is_configured() and srv.build_state()["email_sender"] == "", "must report 'not set up' without credentials"

    hist = srv.load_history()
    url = next(u for u, e in hist.items() if e["application_status"] == "interview")   # set by t_api
    folder = HERE / "_applications" / hist[url]["folder_name"]
    # build the draft exactly as the browser agent does
    sys.modules.pop("apply_browser_agent", None)
    try:
        import apply_browser_agent as agent
    except SystemExit:
        raise Skip("playwright is not installed; e-mail draft writer cannot be imported")
    answers = {"full_name": "Malek Aloui", "email": "applicant@example.invalid", "phone": "+49 1", "_job_title": "Softwareentwickler (m/w/d)"}
    agent.write_email_draft(folder, "hr@employer.invalid", answers, folder / "CV.pdf", folder / "Anschreiben.pdf")
    srv.update_entry(url, application_status="needs_manual", automation_blocker="email_application", apply_email="hr@employer.invalid")

    try:
        srv.send_email_application(url)
        raise AssertionError("sent although no account is configured")
    except RuntimeError:
        pass

    os.environ["JOB_APPLIER_SMTP_USER"] = "applicant@example.invalid"
    os.environ["JOB_APPLIER_SMTP_PASSWORD"] = "abcd efgh ijkl mnop"
    real = mailer.smtplib.SMTP_SSL
    mailer.smtplib.SMTP_SSL = FakeSMTP
    try:
        assert srv.build_state()["email_sender"] == "applicant@example.invalid"
        # the letter is regenerated after the draft was written: the CURRENT file must be sent
        letter = folder / "Anschreiben.pdf"
        letter.write_bytes(letter.read_bytes() + b"\n% regenerated\n")
        out = srv.send_email(srv.UrlBody(url=url))
        assert out["to"] == "hr@employer.invalid", out
        assert len(FakeSMTP.sent) == 1, f"expected exactly one message, got {len(FakeSMTP.sent)}"
        assert FakeSMTP.logins[-1] == ("applicant@example.invalid", "abcdefghijklmnop"), "app password spaces must be stripped"
        msg = FakeSMTP.sent[0]
        assert msg["To"] == "hr@employer.invalid" and "applicant@example.invalid" in msg["From"], (msg["To"], msg["From"])
        assert msg["Subject"].startswith("Bewerbung als") and msg["X-Unsent"] is None and msg["Bcc"] == "applicant@example.invalid"
        names = sorted(a.get_filename() for a in msg.iter_attachments())
        assert names == ["Anschreiben.pdf", "Lebenslauf.pdf"], names
        sent_letter = next(a for a in msg.iter_attachments() if a.get_filename() == "Anschreiben.pdf").get_content()
        assert sent_letter == letter.read_bytes(), "the copy stored in the draft was sent instead of the current letter"
        e = srv.load_history()[url]
        assert e["application_status"] == "submitted" and e["email_sent_at"] and e["email_message_id"], e
        assert "abcd" not in json.dumps(e) and "abcd" not in " ".join(srv.LOG_BUFFER), "password leaked into history or log"
        try:
            srv.send_email(srv.UrlBody(url=url))
            raise AssertionError("the same application was sent twice")
        except HTTPException as exc:
            assert exc.status_code == 400 and "already sent" in str(exc.detail)
        assert len(FakeSMTP.sent) == 1
        # a draft whose recipient does not match the recorded address is refused
        other = next(u for u, e in srv.load_history().items() if u != url)
        srv.update_entry(other, application_status="needs_manual", automation_blocker="email_application", apply_email="someone@else.invalid")
        shutil.copy2(folder / "Bewerbung.eml", HERE / "_applications" / srv.load_history()[other]["folder_name"] / "Bewerbung.eml")
        try:
            srv.send_email(srv.UrlBody(url=other))
            raise AssertionError("sent a draft to an address that does not match the job")
        except HTTPException:
            pass
        assert len(FakeSMTP.sent) == 1
        # Send all: the mismatched draft stays refused, a correct one goes out, re-running sends nothing new
        srv.SEND_PAUSE_S = 0
        third = "https://example.invalid/jobs/9"
        hist = srv.load_history()
        tdir = HERE / "_applications" / "third_job"
        shutil.copytree(folder, tdir)
        agent.write_email_draft(tdir, "jobs@third.invalid", answers, tdir / "CV.pdf", tdir / "Anschreiben.pdf")
        hist[third] = {k: v for k, v in hist[url].items() if not k.startswith("email_")}
        hist[third].update(folder_name="third_job", application_status="needs_manual",
                           automation_blocker="email_application", apply_email="jobs@third.invalid")
        srv.save_history(hist)
        assert set(srv.ready_email_urls()) == {other, third}, srv.ready_email_urls()
        res = srv.run_send_all()
        assert res == {"sent": 1, "failed": 1}, res
        assert len(FakeSMTP.sent) == 2 and FakeSMTP.sent[-1]["To"] == "jobs@third.invalid"
        assert srv.run_send_all() == {"sent": 0, "failed": 1} and len(FakeSMTP.sent) == 2, "re-run must not resend"
    finally:
        mailer.smtplib.SMTP_SSL = real
        for k in ("JOB_APPLIER_SMTP_USER", "JOB_APPLIER_SMTP_PASSWORD"):
            os.environ.pop(k, None)
    return "sent once with both PDFs from the configured account; no duplicate, no wrong recipient, no password leak"


def t_frontend_contract() -> str:
    html = (HERE / "static" / "index.html").read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', html))
    used = set(re.findall(r"\$\('([^']+)'\)", html))
    missing = sorted(used - ids)
    assert not missing, f"script uses element ids that do not exist: {missing}"
    for tab in ("control", "review", "analytics"):
        assert f'data-tab="{tab}"' in html and f'id="tab-{tab}-content"' in html, f"tab {tab} incomplete"
    for m in set(re.findall(r"\bm\.([a-z0-9_]+)", html)):
        assert m in METRIC_KEYS, f"page reads metrics.{m}, which the API does not send"
    for f in set(re.findall(r"\b(?:job|a)\.([a-z_]+)\b", html)) - {"dataset"}:
        assert f in ROW_KEYS, f"page reads row field '{f}', which the API does not send"
    for s in set(re.findall(r"\bs\.([a-z_]+)\b", html)):
        assert s in STATE_KEYS, f"page reads state.{s}, which the API does not send"
    endpoints = set(re.findall(r"api\('(/api/[a-z-]+)'", html))
    assert endpoints <= {"/api/state", "/api/decision", "/api/status", "/api/settings", "/api/send-email", "/api/send-email-all"}, endpoints
    assert "post('/api/send-email-all'" in html, "send-all button is not wired"
    assert "post('/api/send-email'" in html and "confirm(" in html, "manual send must ask for confirmation"
    assert "post('/api/settings'" in html and "post('/api/decision'" in html and "post('/api/status'" in html
    for key in re.findall(r'data-setting="([a-z_]+)"', html):
        assert key in SETTING_KEYS, f"page has a switch for unknown setting '{key}'"
    assert set(re.findall(r'data-setting="([a-z_]+)"', html)) == SETTING_KEYS, "a setting has no switch in the dashboard"
    actions = set(re.findall(r'data-action="([a-z-]+)"', html))
    assert actions == {"discover", "generate", "execute", "auto-cycle"}, actions
    assert html.count("function renderTracker") == 1 and html.count("function fetchState") == 1, "duplicated functions"
    assert "onclick=" not in html, "inline onclick handler found"
    return f"{len(used)} element ids, metric/row/state fields and endpoints match the API"


# ---- browser agent fixtures ----
_BANNER = ('<div id="cookie-banner" style="position:fixed;inset:0;background:rgba(0,0,0,.8);z-index:99"><p>Wir verwenden Cookies</p>'
           '<button onclick="this.parentNode.remove()">Alle akzeptieren</button><button>Einstellungen</button></div>')
_NOISE = ('<form role="search"><input name="q" placeholder="Jobsuche"><button type="submit">Suchen</button></form>'
          '<form class="newsletter"><input type="email" name="nl_email" placeholder="E-Mail"><button type="submit">Abonnieren</button></form>')


def _form(extra: str = "", button: str = "Bewerbung absenden") -> str:
    return ('<form action="/done.html" method="get"><label for="a">Vorname*</label><input id="a" required>'
            '<label for="b">Nachname*</label><input id="b" required><label>E-Mail-Adresse <input name="x1" required></label>'
            '<label for="t">Telefonnummer</label><input id="t">'
            '<div class="upload-box"><span>Lebenslauf hochladen</span><input type="file" name="f1" style="display:none" required></div>'
            '<div class="upload-box"><span>Anschreiben (optional)</span><input type="file" name="f2" style="display:none"></div>'
            f'{extra}<button type="submit">{button}</button></form>')


def _page(body: str) -> str:
    return f'<!doctype html><html><head><meta charset="utf-8"><title>t</title></head><body>{body}</body></html>'


_SOLVE_LATER = ('<div class="g-recaptcha" style="width:300px;height:78px;border:1px solid">captcha</div>'
                '<textarea name="g-recaptcha-response" style="display:none"></textarea>'
                # stands in for the person solving the captcha a few seconds later
                '<script>setTimeout(() => { document.querySelector("[name=g-recaptcha-response]").value = "x".repeat(40); }, 5000)</script>')
_STEP1 = ('<form action="/wizard2.html" method="get"><label for="a">Vorname*</label><input id="a" required>'
          '<label for="b">Nachname*</label><input id="b" required><label>E-Mail <input type="email" name="m" required></label>'
          '<label>Anrede <select name="anrede" required><option value="">Bitte wählen</option><option value="f">Frau</option>'
          '<option value="m">Herr</option></select></label><button type="submit">Weiter</button></form>')
_STEP2 = ('<form action="/done.html" method="get"><label>Lebenslauf <input type="file" name="cv" required></label>'
          '<label>Frühester Eintrittstermin* <input name="start" required></label>'
          '<label>Ihre Nachricht <textarea name="msg"></textarea></label><button type="submit">Bewerbung absenden</button></form>')

FIXTURES = {
    "simple.html": _page(_BANNER + _NOISE + "<h1>Bewerbung</h1>" + _form()),
    "listing.html": _page(_BANNER + _NOISE + "<h1>Werkstudent</h1><a href='simple.html'>Jetzt bewerben</a>"),
    "noform.html": _page(_NOISE + "<h1>Job</h1><p>Bitte bewerben Sie sich telefonisch.</p>"),
    "login.html": _page("<h1>Anmelden</h1><form><input type=email name=email><input type=password name=pw><button type=submit>Anmelden</button></form>"),
    "captcha.html": _page(_form('<div class="g-recaptcha" style="width:300px;height:78px;border:1px solid">captcha</div>'
                                '<textarea name="g-recaptcha-response" style="display:none"></textarea>')),
    "captcha_human.html": _page(_form(_SOLVE_LATER)),
    "wizard.html": _page("<ol class='steps'><li aria-current='step'>1</li><li>2</li></ol>" + _STEP1),
    "wizard2.html": _page("<ol class='steps'><li>1</li><li aria-current='step'>2</li></ol>" + _STEP2),
    "required.html": _page(_form('<label>Führerscheinklasse*<input name=license required></label>')),
    "privacy.html": _page(_form('<label><input type=checkbox name=dsgvo required> Ich stimme der Datenschutzerklärung zu</label>')),
    "email.html": _page("<h1>Softwareentwickler</h1><p>Bewerbung an <a href='mailto:bewerbung@firma-test.invalid'>bewerbung@firma-test.invalid</a></p>"
                        "<a href='mailto:datenschutz@firma-test.invalid'>Datenschutz</a>"),
    "gone.html": _page("<h1>Fehler 404: Seite nicht gefunden</h1><form class='newsletter'><input type=email><button>Senden</button></form>"),
    "trap.html": _page("<h1>Job</h1><div role='button' onclick=\"location='done.html'\">Bewerbung absenden</div>"
                       "<button class='btn' onclick=\"location='done.html'\">Senden</button>"),
    "done.html": _page("<h1>Vielen Dank für Ihre Bewerbung!</h1>"),
}
# page, options -> expected (status, blocker, submitted)
AGENT_CASES = [
    ("simple.html", {"auto_approve": True}, ("submitted", None, True)),
    ("listing.html", {"auto_approve": True}, ("submitted", None, True)),
    ("simple.html", {}, ("awaiting_approval", None, False)),
    ("wizard.html", {"auto_approve": True}, ("submitted", None, True)),
    ("privacy.html", {"auto_approve": True}, ("needs_manual", "required_fields_unfilled", False)),
    ("privacy.html", {"auto_approve": True, "accept_privacy": True}, ("submitted", None, True)),
    ("required.html", {"auto_approve": True}, ("needs_manual", "required_fields_unfilled", False)),
    ("noform.html", {"auto_approve": True}, ("needs_manual", "no_form", False)),
    ("trap.html", {"auto_approve": True}, ("needs_manual", "no_form", False)),
    ("gone.html", {"auto_approve": True}, ("needs_manual", "posting_unavailable", False)),
    ("email.html", {"auto_approve": True}, ("needs_manual", "email_application", False)),
    ("login.html", {"auto_approve": True}, ("needs_manual", "login_required", False)),
    ("captcha.html", {"auto_approve": True}, ("needs_manual", "captcha", False)),
    # Assist mode: the agent waits; the fixture stands in for the person solving the captcha
    ("captcha_human.html", {"auto_approve": True, "assist": True, "human_wait_seconds": 40}, ("submitted", None, True)),
    ("captcha.html", {"auto_approve": True, "assist": True, "human_wait_seconds": 6}, ("needs_manual", "captcha", False)),
]


def t_agent() -> str:
    try:
        import playwright  # noqa: F401
    except ImportError:
        raise Skip("playwright is not installed in this Python environment")
    import apply_browser_agent as agent

    web = Path(tempfile.mkdtemp(prefix="jobapplier_fixtures_"))
    for name, html in FIXTURES.items():
        (web / name).write_text(html, encoding="utf-8")
    docs = web / "docs"
    docs.mkdir()
    (docs / "CV.pdf").write_bytes(b"%PDF-1.4 selftest")
    (docs / "Anschreiben.pdf").write_bytes(b"%PDF-1.4 selftest")

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, directory=str(web), **kw)

        def log_message(self, *a: Any) -> None:
            pass

    httpd = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    wrong: List[str] = []
    ran = 0
    try:
        events: List[str] = []
        for page, opts, expected in AGENT_CASES:
            if opts.get("assist") and not (os.name == "nt" or os.environ.get("DISPLAY")):
                continue   # assist mode needs a visible desktop
            r = agent.apply_to_job(f"http://127.0.0.1:{port}/{page}", max_wait_seconds=20, on_event=events.append,
                                   cv_path=docs / "CV.pdf", letter_path=docs / "Anschreiben.pdf", **opts)
            if r["status"] == "error" and r.get("blocker") == "browser_unavailable":
                raise Skip("no Playwright browser installed (run: playwright install chromium, "
                           "or set JOB_APPLIER_BROWSER_CHANNEL=chrome)")
            got = (r["status"], r["blocker"], r["submitted"])
            if got != expected:
                wrong.append(f"{page} {opts}: expected {expected}, got {got} ({r['note'][:140]})")
            if r["status"] == "needs_manual" and not r["note"].startswith("Complex form detected - manual intervention required"):
                wrong.append(f"{page}: note lacks the structured prefix")
            ran += 1
        if (docs / "Bewerbung.eml").exists():
            eml = (docs / "Bewerbung.eml").read_bytes()
            if b"bewerbung@firma-test.invalid" not in eml or eml.count(b"application/pdf") != 2:
                wrong.append("e-mail draft lacks the recipient or the two PDF attachments")
        else:
            wrong.append("e-mail draft was not written")
        if any(o.get("assist") for _, o, _ in AGENT_CASES) and ran == len(AGENT_CASES) and not any("ACTION NEEDED" in e for e in events):
            wrong.append("assist mode never asked the person to act")
    finally:
        httpd.shutdown()
        shutil.rmtree(web, ignore_errors=True)
    assert not wrong, " | ".join(wrong)
    skipped = len(AGENT_CASES) - ran
    return f"{ran} form scenarios pass" + (f" ({skipped} assist-mode cases need a desktop)" if skipped else "")


def run_inner(with_browser: bool) -> int:
    sys.path.insert(0, str(HERE))
    os.chdir(HERE)
    check("modules compile", t_compile)
    check("modules import", t_imports)
    check("agent safety rules", t_agent_safety)
    check("relevance filter", t_relevance)
    check("Bundesagentur parser (v6 + v4 schema)", t_ba_parser)
    check("Bundesagentur fetch (mocked HTTP)", t_ba_fetch_mocked)
    check("scoring, tiers, scope", t_scoring)
    check("document generation", t_generate)
    check("cover letter content", t_letter_text)
    check("cover letter escaping", t_letter_escaping)
    check("regeneration after history reset", t_regenerate)
    check("cleanup_data rules", t_cleanup)
    check("API state + decisions", t_api)
    check("Execute approval gate", t_execute_gate)
    check("e-mail applications", t_email)
    check("frontend <-> API contract", t_frontend_contract)
    if with_browser:
        check("browser agent form scenarios", t_agent)
    else:
        RESULTS.append(("SKIP", "browser agent form scenarios", "--no-browser"))

    width = max(len(n) for _, n, _ in RESULTS)
    print("\n" + "=" * 78)
    for status, name, detail in RESULTS:
        print(f"{status:4}  {name:<{width}}  {detail}")
    counts = {s: sum(1 for r in RESULTS if r[0] == s) for s in ("PASS", "FAIL", "SKIP")}
    print("=" * 78)
    print(f"{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped")
    return 1 if counts["FAIL"] else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Job Applier end-to-end self-test (runs in a temporary copy)")
    ap.add_argument("--no-browser", action="store_true", help="skip the browser-agent form tests")
    ap.add_argument("--inner", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.inner:
        return run_inner(not args.no_browser)

    root = build_sandbox()
    print(f"Self-test sandbox: {root}  (real data is not touched)")
    cmd = [sys.executable, str(root / "Job" / "selftest.py"), "--inner"] + (["--no-browser"] if args.no_browser else [])
    try:
        return subprocess.call(cmd, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
