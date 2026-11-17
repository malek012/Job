# Changelog

All notable changes to the Job Applier pipeline. Newest first.

## 2026-11-17 — Moved into the Kai workspace

- The project now lives in `D:\Users1\malek\Desktop\Personal\Kai\Projects\Job_applier`.
- `Job/history.json`: 43 stored folder paths rewritten to the new location (backup in `Job/_backup_2026-11-17_move/`).
- `web-ui/Launch-WebUI.bat`: uses `web-ui\venv\Scripts\python.exe` directly. The venv was copied from another folder, so `activate.bat` pointed to a path that no longer exists and the package step installed into the system Python instead of the venv.
- Old logs, `test_nim.py`, `Claude outputs/`, `Job/copy_files.py` and `Job/verify_dashboard.py` (one-off scripts with hard-coded old paths) moved to `_archive/2026-11-17/`.
- Run `create-desktop-icon.bat` again so the "Jobs" icon points to the new folder.

## 2026-11-17 — Desktop icon

- `assets/jobs.ico` (+ `assets/jobs.png`): briefcase with a check badge, emerald→teal.
- `create-desktop-icon.bat`: creates a `Jobs` shortcut on the Desktop that starts `web-ui\Launch-WebUI.bat` directly, with the icon. Replaces the old `Desktop\Jobs.bat` copy (a `.bat` file can't carry its own icon).

## 2026-11-17 — Project rules for AI agents (AGENTS.md)

- `AGENTS.md` at the project root: map, commands, the selftest gate and the hard rules, in the file OpenCode reads automatically.
- The OpenCode manager/worker setup moved out of this project into its own folder, **Kai** (agents `kai-manager`, `kai-manager-backup`, `kai-eyes`, `kai-researcher`, global protocol, installer). This project keeps only its own `AGENTS.md`.

## 2026-11-16 (evening) — Broken cover letters

**What happened.** `cover_applier/build_cover_letter.py` escaped LaTeX special characters one after another and escaped the backslash last, so `&` became `\textbackslash{}&` and `#` became `\textbackslash{}#`. pdfLaTeX then failed, and the builder silently wrote a plain-text PDF (raw LaTeX lines, no layout, ~2 KB) instead. Every job whose company name or title contains `&`, `#`, `%`, `_`, `$`, `{`, `}`, `~` or `^` was affected: 11 letters. The dashboard counted them as ready because it only checked that the file existed. Two of them were e-mailed (Open Grid Europe, Ferchau) on 2026-11-16 15:26 and 15:28.

**Fixes**
- Escaping is done in one pass (`_latex_escape_text`).
- No silent fallback: if pdfLaTeX fails, the build raises with the first LaTeX error and writes no PDF; an old PDF in the folder is deleted before compiling. (`JOB_APPLIER_ALLOW_PLAIN_LETTER=1` re-enables the plain-text fallback for debugging only.)
- New `Job/doc_quality.py`: a letter counts as ready only if it is a pdfTeX-typeset PDF. Generation, approval, Execute and e-mail sending all refuse a letter that fails this check; the dashboard shows the reason instead of "ready".
- E-mail sending attaches the CURRENT `Anschreiben.pdf` and `CV.pdf` from the job folder, not the copies stored inside `Bewerbung.eml` when the draft was written.
- The 11 broken letters were regenerated with the fixed builder; the broken files are kept in `Job/_backup_2026-11-16/broken_letters/`. The three drafts that embedded a broken letter (Open Grid Europe, Ferchau, Röhrich & Partner) were refreshed.
- `selftest.py`: new "cover letter escaping" check (special characters, LaTeX failure must raise, fallback letters detected); the e-mail check verifies the current letter is sent. The earlier "document generation" check passed on a fallback letter; it now requires a typeset one.

## 2026-11-16 (afternoon) — Sending e-mail applications

- New `Job/mailer.py`: sends a job's `Bewerbung.eml` through Gmail SMTP with an app password from `Job/.env` (`JOB_APPLIER_SMTP_USER`, `JOB_APPLIER_SMTP_PASSWORD`). `python mailer.py --check` tests the login without sending.
- Dashboard switch **Auto-send e-mail applications** (off by default): when the agent finds an application address (after you solve the captcha in Assist mode), the e-mail is sent right away, 20 s apart, and the job becomes `submitted`.
- Tracker button **Send e-mail now** for drafts that already exist; asks for confirmation.
- **Send all ready e-mails** button at the top of the Tracker: sends every prepared, unsent e-mail application in the background, 20 s apart, Tier 1 first, with a live counter. Lists all recipients in the confirmation. Runs alongside Execute. `POST /api/send-email-all`.
- Safeguards: each job is sent at most once (`email_sent_at`); the draft's recipient must match the address recorded for that job; both PDFs must be attached; the password never appears in history or logs. Covered by `selftest.py`.
- Not built, by decision: automatic solving of the Bundesagentur captcha. It stays a step for the person in Assist mode.
- Not yet verified: a real send through Gmail (no app password set yet).

## 2026-11-16 — Assist mode, multi-step forms, relevance filter, Bundesagentur v6

### Browser agent (`Job/apply_browser_agent.py`, v3)
- **Assist mode.** Visible browser with a persistent profile (`Job/_browser_profile/`). On a captcha, a login wall or a bot-protection page the agent pauses, shows what to do (banner in the browser, banner + log line in the dashboard), waits until the person has cleared it, then continues by itself: fill, next, submit. The agent never solves, types into or bypasses a captcha and never types a password.
- **Multi-step forms.** Fills a step, presses "Weiter / Next", checks that the form advanced and that the site showed no validation error, up to 8 steps, then applies the same final gates as before.
- **More fields.** Street, country, salary, earliest start, birth date, LinkedIn, GitHub, website, message/cover-letter text boxes, and salutation/country drop-downs. Values come from `Job/application_answers.json`; an empty answer is never typed and a required field without an answer stops as `needs_manual`.
- **Privacy consent** is a switch (off by default) instead of an environment variable only.
- **Dead postings** (HTTP 404/410 or a "not found / no longer available" page) end as blocker `posting_unavailable` and are moved to status `dropped`.
- **E-mail applications.** If a posting only gives an application e-mail address, the agent writes `Bewerbung.eml` (recipient, subject, text, both PDFs attached) into the job folder; blocker `email_application`.
- **Job boards.** Uses the on-page form if there is one, else the posting's apply button, else the link to the employer's own application page. Handles the Arbeitnow "Einwilligen" consent layer and the Bundesagentur "Info zur Bewerbung" button.
- The required-field check is limited to the application form itself (a newsletter e-mail box elsewhere on the page no longer blocks).
- **Removed** three fallbacks added on 2026-11-16 00:50–01:30 because they could press a submit button on a page that was never recognised as an application form: the "click any element whose class contains btn / text contains senden or submit" apply-button fallbacks, and the accordion expander that clicked every element mentioning "Bewerbung". `selftest.py` now fails if they return.

### Server and dashboard
- New Execute switches in the dashboard: **Assist mode**, **Auto-submit**, **Accept privacy consent**. Stored in `Job/settings.json`; all off by default. `POST /api/settings`.
- `/api/state` now carries `settings` and `attention` (what the person has to do right now) instead of `auto_submit`; rows carry `blocker` and `apply_email`.
- Dashboard restored to the event-delegated version (the 00:50 rewrite had brought back the 404 PDF links, the wrong "Pending" number and inline handlers with job URLs) and extended: Tracker shows what happened and the next step per application, with a Retry button; amber banner while the agent waits for the person.
- Discovery runs as a subprocess, so the scraper's output is visible in the log and code changes apply without a restart.

### Pipeline
- **Relevance filter** (`Job/relevance.py`): only software, ML/AI, computer vision, robotics, embedded and AIT engineering titles get documents; sales, customer success, marketing, finance, consulting and similar are dropped at discovery and again before generation.
- **Bundesagentur source fixed.** API v4 was retired; the scraper now calls v6 and reads its schema (`ergebnisliste`, `stellenangebotsTitel`, `firma`, `referenznummer`, `externeURL`). Training courses and vocational offers are excluded. Queries cover Dortmund + 50 km.
- Cover letters no longer mention a Master's degree (template header, profile, and a guard in the generator). The letter file is now written as `Anschreiben.pdf` on every OS.
- History reset: the 10 entries without PDFs were removed for regeneration; 6 non-technical entries were set to `skipped`.

### Tests
- `python selftest.py` runs 15 checks in a temporary copy with synthetic data: compile, imports, agent safety rules, relevance, Bundesagentur parser, scoring, generation, letter content, regeneration, cleanup rules, API, Execute gate, dashboard/API contract, and 15 browser form scenarios (single page, listing, wizard, consent, required field, no form, blind-click trap, dead posting, e-mail application, login, captcha, captcha cleared by the person in assist mode, captcha timeout).

### Verification status
- All selftest checks pass in the sandbox; the 15 browser scenarios pass in a real Chrome against local test pages.
- Bundesagentur v6 was verified from the Jobsuche site and the scraper has since harvested 33 postings in a real run.
- **Assist mode has not yet been run against a real job site.**

### Known open items
- Bundesagentur detail pages have no form: they lead to an employer link, an e-mail address or contact data behind the site's own captcha. Expect `email_application`, `captcha` (assist) or `no_form` there.
- `cv_werkstudent.pdf` still says "Masterstudent / Vorbereitung Master" in its header; only the cover letter was changed.
- `job_analyzer.py` cannot import `src.utils.llm_provider` from `Job/`, so the LLM analysis always falls back to heuristics (it is not triggered at the moment, because no source supplies job descriptions).
- `verify_dashboard.py`, `temp_fix.py`, `apply_browser.py`, `status_tracker.py`, `run_pipeline.py`, `organizer.py` are stale (undefined names or old status names) and unused by the dashboard.
- SoPra entry in the CV: subtitle line overlaps the first bullet.

## 2026-11-15 — Dashboard repair, approval gating, browser-agent rewrite, data cleanup

### Dashboard (`Job/static/index.html`)
- Rewrote the page script. Every function had been pasted in two or three times, and the Tracker tab called `renderAnalytics()` without data, which threw `Cannot read properties of undefined (reading 'tier1_count')`.
- Fixed tab switching: it looked for the wrong element ids and hid every panel, leaving a blank page.
- All three tabs now render with zero data, with missing metrics (`tier1_count`, `status_breakdown`), and with the server offline.
- Approve / Reject work; the page and the server previously disagreed on the request format.
- "Approve Selected" / "Reject Selected" are wired to row checkboxes (they did nothing before).
- Application Tracker tab has a table of all applications with a status dropdown.
- PDF links point at the job's real application folder (`/applications/<folder_name>/CV.pdf`) instead of a URL-encoded job link that always returned 404.
- "System Online" reflects the real connection; action buttons disable while a task runs; the error banner can be dismissed.
- No inline `onclick` handlers and no job URLs inside HTML attributes; all dynamic text is escaped.

### Server (`Job/api_server.py`)
- Discover and Auto Cycle no longer crash: both called `update_targets_file`, which was imported under a different name, and the import was shadowed by a route function.
- **Strict approval gating.** New status `approved`. Approving used to leave a job as `pending`, and Execute processed every `pending` job. Execute now only processes `approved` jobs. Rejecting sets `skipped`.
- Approval is refused when `CV.pdf` / `Anschreiben.pdf` are missing on disk. Document readiness is checked on disk, not read from the stored `docs_ready` flag.
- One pipeline task at a time (HTTP 409 while busy). History updates are per-entry read-modify-write under a lock, so status changes no longer overwrite each other.
- Generate Docs runs `batch_processor.py --no-discover` and streams its output live into the dashboard log.
- New endpoints `POST /api/decision` (`{urls, decision}`) and `POST /api/status` (`{url, status, note}`) replace the routes that carried the job URL in the path.
- Submission is off by default (`JOB_APPLIER_AUTO_SUBMIT`); optional visible browser (`JOB_APPLIER_HEADED`).
- Accepts `--port`; the page is served with `Cache-Control: no-store`; logging no longer crashes on cp1252 consoles.
- The server starts even when Playwright is not installed.

### Browser agent (`Job/apply_browser_agent.py`, rewritten)
- Uses only the job's own documents. It previously uploaded the most recently modified CV and cover letter from any folder.
- Heuristic form detection from labels, names, ids, placeholders and autocomplete hints, in the page and its iframes: first name, last name, full name, e-mail, phone, postcode, city (German and English). Search and newsletter fields are ignored.
- File inputs are classified as CV or cover letter from surrounding text, including hidden inputs behind styled upload buttons.
- Follows a listing page's "Jetzt bewerben" / "Apply" button (same tab or new tab, at most two hops).
- Consent banners: OneTrust, Cookiebot, Usercentrics, Didomi, consentmanager, Borlabs, CookieYes, StepStone, plus a text fallback restricted to consent layers.
- Pre-submit gates: submit is clicked only if the CV uploaded, contact fields were filled, no required field is empty, and the button belongs to the application form. `submitted` is reported only when a confirmation message is detected.
- Structured blocker reporting: status `needs_manual` with a blocker code (`captcha`, `bot_protection`, `login_required`, `multi_step_form`, `no_form`, `required_fields_unfilled`, `no_submit_button`, `validation_errors`, `submission_unconfirmed`) and a note starting "Complex form detected - manual intervention required".
- One `RESULT {...}` JSON log line per attempt and a screenshot saved as `apply_result.png` in the job's folder.
- Fixed the original selectors (`input[name*="first"] i`), which could never match.
- New settings: `JOB_APPLIER_ACCEPT_PRIVACY`, `JOB_APPLIER_BROWSER_CHANNEL`, `JOB_APPLIER_BROWSER_PATH`.

### Pipeline (`Job/batch_processor.py`, `Job/job_scraper.py`)
- Whole-word keyword matching. "ai" no longer matches "gmail" (which inflated every score through the candidate's own e-mail address), and "essen" no longer matches "Interessen".
- Tier 1 cities are recognised in URL spellings without umlauts (Koln / Koeln, Dusseldorf / Duesseldorf).
- Sort order fixed: Tier 1 before Tier 2 (Tier 2 sorted first before).
- Match score counts technology overlap against the profile keyword list, or against `profile.json` → `skills` when that list exists.
- History is saved after each job and merged into the latest file on disk, so a crash mid-batch loses nothing and dashboard decisions made during a run are kept.
- Duplicate guard now derives from `history.json` only: removing a URL from history makes the job eligible for regeneration. `previously_done.txt` is no longer used.
- New application folders get a short hash suffix so similar names cannot share a folder.
- `--no-discover` flag; pause between jobs configurable (`JOB_APPLIER_PAUSE_SECONDS`, default 2, was 20).
- CLI `--execute` processes `approved` jobs with their own documents; it previously called a function that was never imported.
- Scraper: fixed the `hashId` typo that aborted Bundesagentur queries; an unreadable `targets.json` now raises instead of being treated as empty and overwritten; atomic writes; BOM-safe reads; Stellenwerk account/booking pages are skipped; Playwright is imported only when needed.

### Data
- Added `Job/cleanup_data.py` (dry run by default).
- Removed 8 records from `history.json` and 8 from `targets.json`: the fabricated StepStone ids 13900001–13900007 and one Stellenwerk "account/book" page that was not a job posting. Result: 10 history entries, 15 targets.
- The 10 remaining history entries have no PDFs on disk and are shown as "PDFs missing". They were left in place; `python cleanup_data.py --reset-missing-docs --apply` drops them so Generate Docs rebuilds them.

### Launcher (`web-ui/Launch-WebUI.bat`)
- Resolves `api_server.py` relative to the script, so it works from any working directory.

### Backups
- `Job/_backup_2026-11-15/` — original `api_server.py`, `batch_processor.py`, `job_scraper.py`, `apply_browser_agent.py`, `static/index.html`, `history.json`, `targets.json`, `Launch-WebUI.bat`, plus `*.after_audit.py` copies taken before the second round of changes.
- `Job/_backup_cleanup/` — timestamped copies of `history.json` and `targets.json` taken by `cleanup_data.py` before each write.

### Verification status
- Dashboard and API: tested against a copy of the data, then confirmed on the live server (page loads, three tabs render, state matches the cleanup).
- Document regeneration after removing history entries: tested on a copy.
- Browser agent: passed eight local test pages in Chrome (plain form, listing with apply button, dry run, no form, login wall, captcha, multi-step, required fields). **Not yet run against a real job site.**
- Discover (live job sources): **not yet run** since the fixes.

### Known open items
- Most real postings will return `needs_manual` (logins, multi-step portals, required consent checkboxes).
- Cover letters are the same text for every job; the scraper does not collect job descriptions, so the LLM analysis never runs.
- A failed document generation is only written to `failed_jobs.log`, not shown in the dashboard.
- The virtual environment was created at `C:\Users\malek\web-ui\venv` and later moved, so the launcher's package-check step does not maintain the environment the server runs from.
- Leftover scripts (`temp_fix.py`, `check_api.py`, `verify_final.py`, `static/debug.py`, `static/verify_html.py`, `test_nim.py`) and older parallel implementations (`apply.py`, `apply_browser.py`, `run_pipeline.py`, `organizer.py`) are still present. `apply_browser.py` and `status_tracker.py` use the old status names.
- `opencode-server.log` contains a server password in plain text.
