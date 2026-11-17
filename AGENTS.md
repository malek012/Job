# Job Applier — project rules for AI agents

Read this together with Kai's global protocol (~/.config/opencode/AGENTS.md).
Full manual: `README.md`. History of changes and known problems: `CHANGELOG.md`.

## What this is
A local pipeline that finds job postings, generates a CV + cover letter per job,
lets the user approve each one in a dashboard, then applies (web form or e-mail).
Applicant: Malek Aloui. Nothing may reach an employer without the user's approval.

## Map
- `Job/api_server.py` — FastAPI server + dashboard backend, http://127.0.0.1:7788
- `Job/static/index.html` — dashboard (Control Center, Review, Application Tracker)
- `Job/job_scraper.py` — discovery (Bundesagentur API v6, Arbeitnow, Stellenwerk, StepStone)
- `Job/relevance.py` — technical-role filter; `Job/batch_processor.py` — scoring + document generation
- `Job/cover_applier/build_cover_letter.py` — LaTeX cover letter; `Job/doc_quality.py` — rejects untypeset letters
- `Job/apply_browser_agent.py` — fills web forms; assist mode hands captchas/logins to the user
- `Job/mailer.py` — sends e-mail applications via Gmail (credentials only in `Job/.env`)
- State: `Job/targets.json` (array), `Job/history.json` (object keyed by job URL),
  `Job/_applications/<folder>/` (CV.pdf, Anschreiben.pdf, Bewerbung.eml), `Job/settings.json`
- `web-ui/` is a separate third-party project: do not edit it, except `Launch-WebUI.bat`.

## Commands (Windows, run from `Job\`)
- Python: `..\web-ui\venv\Scripts\python.exe`
- Self-test (MANDATORY after every code change, must end with `0 failed`):
  `..\web-ui\venv\Scripts\python.exe selftest.py`
  It runs in a temporary copy; it never touches real data or contacts employers.
- Mail login check (sends nothing): `..\web-ui\venv\Scripts\python.exe mailer.py --check`
- Server: `..\web-ui\venv\Scripts\python.exe api_server.py --port 7788` (restart after .py changes)
- UI check: call `kai-eyes` with http://127.0.0.1:7788, tabs "Control Center",
  "Review & PDF Preview", "Application Tracker"; expect no console errors.

## Hard rules for this project
1. Never weaken the approval gate: Execute / Auto cycle / e-mail sending act only on
   jobs the user approved, and approval needs both PDFs to pass `doc_quality`.
2. Never switch on Auto-submit, Assist mode, privacy consent or Auto-send e-mail
   (dashboard switches, `settings.json` or env vars) on your own.
3. Never run Execute, send e-mail, or open employer sites with the agent unless the
   user asks in that session. Never send the same application twice.
4. No captcha solving or bypassing, no "click anything that looks like a button"
   fallbacks in the browser agent, never type passwords. `selftest.py` checks this.
5. Do not replace `static/index.html` wholesale. It must keep reading `settings`,
   `attention`, `applications`, `blocker`, `doc_problem` from `/api/state`.
   Change it in place, then run selftest + kai-eyes.
6. Text that goes into LaTeX must go through `_latex_escape_text` (one pass).
   A failed LaTeX build must fail loudly; never re-add a silent plain-text fallback.
7. No invented job URLs/IDs in `targets.json` / `history.json`. Test with
   `selftest.py` or a copy of the folder, never by editing real state.
8. Back up before rewriting a module or a state file; write state files atomically.

## Privacy (free model endpoints may log prompts)
Do NOT open or paste into prompts: `**/.env`, `Job/_applications/**` (personal
documents and e-mails), `Job/_browser_profile/**`, `Job/settings.json`. Read
`Job/history.json` only for the entries the task needs. Never print e-mail
passwords or API keys.

## Known open items (check CHANGELOG.md before "fixing" these)
- `cv_werkstudent.pdf` header still mentions a Master's degree.
- Most Bundesagentur postings need a captcha the user solves in assist mode.
- `verify_dashboard.py`, `temp_fix.py`, `apply_browser.py`, `status_tracker.py`,
  `run_pipeline.py`, `organizer.py` are stale and unused by the dashboard.
