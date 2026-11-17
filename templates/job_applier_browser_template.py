"""
templates/job_applier_browser_template.py
==========================================
Reusable template: tie the Job Applier logic into the browser-use agent runner.

Bridge between:
  1. browser-use agent  (web-ui/src)  -> drives the browser
  2. Job Applier bot    (Job/)        -> builds CV + cover letter, tracks targets

Hook modes (env JOB_APPLIER_HOOK_MODE or --hook-mode):
  discover : browser agent surfs job boards, extracts job URLs -> Job/targets.json
  apply    : browser agent fills application forms using the generated
             CV / cover letter / apply_data.txt
  full     : discover -> build documents (batch_processor) -> apply   [default]

Env (see web-ui/.env):
  NVIDIA_API_KEY=nvapi-...         # NVIDIA NIM API key
  OPENAI_API_BASE=https://integrate.api.nvidia.com/v1
  DEFAULT_LLM_MODEL=nvidia/nemotron-3-ultra-550b-a55b
  JOB_APPLIER_HOOK_MODE            # discover | apply | full
  JOB_APPLIER_MAX_JOBS             # default 15  (note: batch_processor.py hardcodes 15)
  JOB_APPLIER_HEADLESS             # 1 = headless browser
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
TEMPLATE_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = TEMPLATE_DIR.parent
JOB_DIR = WORKSPACE_ROOT / "Job"
WEB_UI_DIR = WORKSPACE_ROOT / "web-ui"

# Make web-ui/src importable so we can reuse the codebase's agent/browser pieces
SRC = WEB_UI_DIR / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

TARGETS_PATH = JOB_DIR / "targets.json"
BATCH_PROCESSOR = JOB_DIR / "batch_processor.py"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv(WEB_UI_DIR / ".env", override=False)

HOOK_MODE = os.getenv("JOB_APPLIER_HOOK_MODE", "full").lower()
MAX_JOBS = int(os.getenv("JOB_APPLIER_MAX_JOBS", "15"))
HEADLESS = os.getenv("JOB_APPLIER_HEADLESS", "1") == "1"
LLM_MODEL = os.getenv("DEFAULT_LLM_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "https://integrate.api.nvidia.com/v1")

if not NVIDIA_API_KEY or "PLACEHOLDER" in NVIDIA_API_KEY:
    print("WARNING: NVIDIA_API_KEY is not set with a real nvapi... key in web-ui/.env")


# ---------------------------------------------------------------------------
# Job Applier helpers
# ---------------------------------------------------------------------------
def write_targets(urls: List[str]) -> None:
    """Write discovered job URLs into Job/targets.json for the Job Applier."""
    targets = [{"url": u} for u in urls]
    TARGETS_PATH.write_text(json.dumps(targets, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[targets.json] Wrote {len(targets)} job URL(s) -> {TARGETS_PATH}")


def run_job_applier(max_jobs: int = MAX_JOBS) -> int:
    """Run the Job Applier batch processor (builds CV + cover letter per target).

    Note: batch_processor.py hardcodes MAX_JOBS = 15 and PAUSE_SECONDS = 20;
    edit Job/batch_processor.py to tune those values.
    """
    if not BATCH_PROCESSOR.exists():
        print(f"[ERROR] batch_processor.py not found: {BATCH_PROCESSOR}")
        return 1
    print(f"[Job Applier] Running batch_processor.py (max_jobs={max_jobs}) ...")
    result = subprocess.run([sys.executable, "-u", str(BATCH_PROCESSOR)], cwd=str(JOB_DIR))
    return result.returncode


# ---------------------------------------------------------------------------
# Browser agent hook (pluggable - CUSTOMIZE HERE)
# ---------------------------------------------------------------------------
async def run_browser_agent(task: str) -> None:
    """
    Drive a browser-use agent with the NVIDIA NIM free-tier model (Nemotron).

    Uses get_llm_model() from src.utils.llm_provider (provider "nvidia") which
    pulls model/api_key/base_url strictly from env vars. No keys hardcoded.
    """
    try:
        from browser_use.agent.service import Agent
        from browser_use.browser.browser import Browser, BrowserConfig
        from browser_use.browser.context import BrowserContext, BrowserContextConfig
        from src.utils import llm_provider

        llm = llm_provider.get_llm_model(
            provider="nvidia",
            model_name=LLM_MODEL,
            temperature=0.0,
            base_url=OPENAI_API_BASE,
            api_key=NVIDIA_API_KEY,
        )
        if llm is None:
            raise RuntimeError("get_llm_model returned None for provider 'nvidia'")

        browser = Browser(config=BrowserConfig(headless=HEADLESS))
        context = BrowserContext(browser=browser, config=BrowserContextConfig())
        agent = Agent(task=task, llm=llm, browser=browser, browser_context=context)
        await agent.run(max_steps=100)
        await browser.close()
    except Exception as exc:  # pragma: no cover - template hook, customized per install
        print(f"[browser agent] Could not run Agent automatically: {exc}")
        print("    -> Wire your task into run_browser_agent() in this template,")
        print("    -> or launch the Web UI (Launch-WebUI.bat) and trigger the agent there.")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def discover_jobs() -> List[str]:
    """Read current job URLs from Job/targets.json (filled by the browser agent)."""
    if not TARGETS_PATH.exists():
        return []
    try:
        data = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [t.get("url") for t in data if t.get("url")]


def main() -> int:
    parser = argparse.ArgumentParser(description="Job Applier + browser-use unified template")
    parser.add_argument("--hook-mode", choices=["discover", "apply", "full"], default=HOOK_MODE)
    parser.add_argument("--max-jobs", type=int, default=MAX_JOBS)
    parser.add_argument("--task", help="Custom task string for the browser agent")
    args = parser.parse_args()

    mode = args.hook_mode
    print(f"[template] hook_mode={mode}  max_jobs={args.max_jobs}  model={LLM_MODEL}")

    urls: List[str] = []
    if mode in ("discover", "full"):
        task = args.task or (
            "Go to major German job boards (StepStone, Indeed.de, Bundesagentur für Arbeit), "
            "collect Werkstudent and Vollzeit software/IT job postings across ALL of Germany "
            "(not just one city), and return a list of job detail URLs."
        )
        asyncio.run(run_browser_agent(task))
        urls = discover_jobs()
        write_targets(urls)

    if mode in ("apply", "full"):
        if not urls and not TARGETS_PATH.exists():
            print("[apply] No targets found. Run with --hook-mode discover first,")
            print("        or fill Job/targets.json manually.")
            return 1
        return run_job_applier(max_jobs=args.max_jobs)

    return 0


if __name__ == "__main__":
    sys.exit(main())