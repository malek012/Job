"""
Job/apply_browser.py — Real browser-based application submitter.

Uses browser-use agent + NVIDIA NIM to:
  1. Open each approved job's URL in a browser
  2. Fill out the application form with candidate data
  3. Upload CV.pdf and Anschreiben.pdf from the job's folder
  4. Submit the application
  5. Track result in history.json (submitted / failed / needs_manual)

Usage:
    from apply_browser import run_apply_batch
    run_apply_batch(history)  # processes all approved, docs-ready jobs
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Make web-ui/src importable for llm_provider
WEB_UI_SRC = Path(__file__).resolve().parent.parent / "web-ui" / "src"
if str(WEB_UI_SRC) not in sys.path:
    sys.path.insert(0, str(WEB_UI_SRC))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / "web-ui" / ".env", override=False)

WORKSPACE_ROOT = Path(__file__).resolve().parent
HISTORY_PATH = WORKSPACE_ROOT / "history.json"

PROFILE = {
    "name": "Malek Aloui",
    "email": "malekaloui1777@gmail.com",
    "phone": "+49 176 84243799",
    "location": "Dortmund 44263, Germany",
    "status": "B.Sc. Informatik abgeschlossen (TU Dortmund 2026), Master angestrebt",
    "cover_letter_note": "Bewerbung als Werkstudent / Vollzeit Softwareentwicklung",
}

# ── Helpers ─────────────────────────────────────────────────────────────────

def load_history() -> Dict[str, Any]:
    if HISTORY_PATH.exists():
        with HISTORY_PATH.open("r", encoding="utf-8-sig") as f:
            return json.load(f)
    return {}


def save_history(history: Dict[str, Any]) -> None:
    import tempfile
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=HISTORY_PATH.parent, delete=False, suffix=".tmp",
    ) as tmp:
        json.dump(history, tmp, ensure_ascii=False, indent=2)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_path = Path(tmp.name)
    os.replace(temp_path, HISTORY_PATH)


def get_approved_jobs(history: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """Return (url, entry) for jobs that are approved, pending, and docs-ready."""
    approved = []
    for url, entry in history.items():
        if (entry.get("application_status") == "pending"
                and entry.get("docs_ready", False)
                and entry.get("status") != "submitted"):
            approved.append((url, entry))
    # Sort: Tier 1 first, then highest match score
    approved.sort(
        key=lambda item: (
            -item[1].get("tier", 0),
            -item[1].get("match_score", 0),
        )
    )
    return approved


# ── Browser-use application agent ───────────────────────────────────────────

async def apply_to_job(
    url: str,
    entry: Dict[str, Any],
    max_steps: int = 60,
) -> Dict[str, Any]:
    """
    Use browser-use agent to navigate to the job URL, fill the form,
    upload CV + cover letter, and submit.

    Returns a result dict with: submitted (bool), note (str), steps (int).
    """
    try:
        from browser_use.agent.service import Agent
        from browser_use.browser.browser import Browser, BrowserConfig
        from browser_use.browser.context import BrowserContext, BrowserContextConfig
        from src.utils import llm_provider
    except ImportError as exc:
        return {
            "submitted": False,
            "note": f"browser-use import error: {exc}",
            "steps": 0,
        }

    job_dir = Path(entry.get("job_dir", ""))
    cv_path = job_dir / "CV.pdf"
    letter_path = job_dir / "Anschreiben.pdf"
    apply_data_path = job_dir / "apply_data.txt"

    cv_str = str(cv_path) if cv_path.exists() else ""
    letter_str = str(letter_path) if letter_path.exists() else ""
    job_title = entry.get("job_title", "die Stelle")
    company = entry.get("company", "Unknown")
    location = entry.get("location", "")

    # Read apply_data.txt for salary/notes if available
    salary_note = ""
    if apply_data_path.exists():
        try:
            salary_note = apply_data_path.read_text(encoding="utf-8").strip()[:300]
        except Exception:
            pass

    task = (
        f"You are applying for a job on behalf of {PROFILE['name']} "
        f"({PROFILE['email']}, {PROFILE['phone']}).\n\n"
        f"TARGET JOB:\n  URL: {url}\n  Company: {company}\n  Title: {job_title}\n  Location: {location}\n\n"
        f"TASK STEPS:\n"
        f"1. Open the job URL in the browser.\n"
        f"2. Find and click the application / 'Jetzt bewerben' / 'Apply' button.\n"
        f"3. Fill out the application form with:\n"
        f"   - Name: {PROFILE['name']}\n"
        f"   - Email: {PROFILE['email']}\n"
        f"   - Phone: {PROFILE['phone']}\n"
        f"   - Location: {PROFILE['location']}\n"
        f"   - Status: {PROFILE['status']}\n"
        f"   - Cover letter note: {PROFILE['cover_letter_note']}\n"
        f"   - Salary info: {salary_note}\n"
        f"4. Upload the CV file: {cv_str}\n"
        f"5. Upload the cover letter file: {letter_str}\n"
        f"6. Submit the application.\n"
        f"7. Report whether submission succeeded or if a manual step is required.\n\n"
        f"IMPORTANT: If you hit a login wall, CAPTCHA, or a field you cannot fill, "
        f"stop and report 'needs_manual' with a clear note. Do not invent data.\n"
    )

    llm = llm_provider.get_llm_model(
        provider="nvidia",
        model_name=os.getenv("DEFAULT_LLM_MODEL", "nvidia/nemotron-3-ultra-550b-a55b"),
        temperature=0.1,
        base_url=os.getenv("OPENAI_API_BASE", "https://integrate.api.nvidia.com/v1"),
        api_key=os.getenv("NVIDIA_API_KEY", ""),
    )
    if llm is None:
        return {"submitted": False, "note": "LLM initialization failed", "steps": 0}

    browser = Browser(config=BrowserConfig(headless=True))
    context = BrowserContext(browser=browser, config=BrowserContextConfig())
    agent = Agent(task=task, llm=llm, browser=browser, browser_context=context)

    try:
        history_list = await agent.run(max_steps=max_steps)
        await browser.close()

        # Inspect the agent history for a success/failure signal
        final_note = "completed via browser agent"
        submitted = False
        try:
            # Try to read last actions for keywords
            actions_text = str(history_list)
            low = actions_text.lower()
            if any(k in low for k in ["submitted", "successfully", "application sent", "bewerbung abgeschlossen", "eingereicht"]):
                submitted = True
                final_note = "Application submitted via browser agent."
            elif any(k in low for k in ["captcha", "login", "manual", "could not", "unable", "blocked"]):
                submitted = False
                final_note = "Browser agent needs manual intervention (CAPTCHA/login/blocked)."
            else:
                submitted = False
                final_note = "Browser agent completed without clear submission confirmation."
        except Exception:
            pass

        return {
            "submitted": submitted,
            "note": final_note,
            "steps": len(history_list) if hasattr(history_list, "__len__") else 0,
        }

    except Exception as exc:
        try:
            await browser.close()
        except Exception:
            pass
        return {"submitted": False, "note": f"Browser agent error: {exc}", "steps": 0}


# ── Batch runner ────────────────────────────────────────────────────────────

def run_apply_batch(history: Optional[Dict[str, Any]] = None) -> str:
    """
    Apply to all approved, docs-ready jobs using the browser agent.

    Updates history.json with submission status.
    Returns a summary string.
    """
    if history is None:
        history = load_history()

    approved = get_approved_jobs(history)
    if not approved:
        return "No approved, docs-ready applications to send. Review and approve jobs in Tab 2 first."

    results: List[str] = []
    submitted_count = 0
    failed_count = 0
    manual_count = 0

    for url, entry in approved:
        company = entry.get("company", "Unknown")
        job_title = entry.get("job_title", "")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(apply_to_job(url, entry))
        finally:
            loop.close()

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry["status_updated_at"] = now

        if result["submitted"]:
            entry["application_status"] = "submitted"
            entry["application_status_label"] = "Submitted"
            entry["status_source"] = "browser_agent"
            entry["submission_note"] = result["note"]
            submitted_count += 1
            results.append(f"  ✅ {company} — {job_title}: submitted")
        elif "manual" in result["note"].lower() or "needs_manual" in result["note"].lower():
            entry["application_status"] = "pending"
            entry["application_status_label"] = "Needs Manual Finish"
            entry["status_source"] = "browser_agent_manual"
            entry["submission_note"] = result["note"]
            manual_count += 1
            results.append(f"  ⚠️ {company} — {job_title}: needs manual finish")
        else:
            entry["application_status"] = "failed"
            entry["application_status_label"] = "Submission Failed"
            entry["status_source"] = "browser_agent_failed"
            entry["submission_note"] = result["note"]
            failed_count += 1
            results.append(f"  ❌ {company} — {job_title}: failed")

    save_history(history)

    summary = (
        f"🚀 Application batch complete.\n"
        f"   ✅ Submitted: {submitted_count}\n"
        f"   ⚠️ Needs manual: {manual_count}\n"
        f"   ❌ Failed: {failed_count}\n"
    )
    summary += "\n".join(results)
    return summary


if __name__ == "__main__":
    print(run_apply_batch())
