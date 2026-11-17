# Job Applier

A local pipeline that discovers job postings, generates a CV and cover letter per job, lets you review and approve each application in a dashboard, and then fills in the application form in a browser.

Nothing is sent to an employer unless you approve the job **and** turn on auto-submit (or submit the filled form yourself).

See `CHANGELOG.md` for what changed and what is still open. Rules for AI coding agents are in `AGENTS.md`; the OpenCode setup (Kai) lives in its own folder outside this project.

---

## 1. System architecture

```
Job_applier/
├── README.md, CHANGELOG.md
├── .env                        Telegram / OpenCode bot settings (not used by the pipeline)
├── Job/                        the pipeline
│   ├── api_server.py           FastAPI server + dashboard backend (port 7788)
│   ├── static/index.html       dashboard (Control Center, Review, Application Tracker)
│   ├── job_scraper.py          discovery: Bundesagentur API, Arbeitnow API, Stellenwerk, StepStone
│   ├── batch_processor.py      scoring, tiering, document generation
│   ├── apply_browser_agent.py  form detection, filling, upload, guarded submit
│   ├── job_analyzer.py         optional LLM analysis of job descriptions
│   ├── relevance.py            domain gate: technical roles only
│   ├── doc_quality.py          rejects cover letters that are not properly typeset
│   ├── mailer.py               sends Bewerbung.eml drafts via Gmail (app password in Job\.env)
│   ├── selftest.py             end-to-end self-test in a temporary copy
│   ├── application_answers.json  answers for extra form fields (salary, start date, ...)
│   ├── settings.json           Execute switches set in the dashboard
│   ├── _browser_profile/       remembered logins/cookies for Assist mode
│   ├── cleanup_data.py         removes test/invalid records, resets entries with missing PDFs
│   ├── cv_applier/             static CV variants (werkstudent / vollzeit / duales Studium)
│   ├── cover_applier/          cover-letter builder (pdflatex, reportlab fallback)
│   ├── profile.json            candidate contact data (optional "skills" list for scoring)
│   ├── targets.json            discovered jobs   — JSON array of {url, company, job_title, location, source}
│   ├── history.json            processed jobs    — JSON object keyed by job URL
│   ├── _applications/          one folder per job: CV.pdf, Anschreiben.pdf, apply_result.png, ...
│   ├── _backup_2026-11-15/     originals from before the November 2026 rework
│   ├── _backup_cleanup/        timestamped copies written by cleanup_data.py
│   ├── failed_jobs.log         document-generation failures
│   └── .env                    OPENROUTER_API_KEY
└── web-ui/                     third-party browser-use web UI (own git repo)
    ├── Launch-WebUI.bat        launcher for the dashboard
    ├── venv/                   Python 3.11 environment the server runs from
    └── .env                    NVIDIA_API_KEY and LLM settings
```

### State files

| File | Role | Written by |
|---|---|---|
| `targets.json` | every job discovered, whether or not it is in scope | `job_scraper.py`, `cleanup_data.py` |
| `history.json` | one entry per job that got documents; holds status, tier, score, folder name | `batch_processor.py`, `api_server.py`, `apply_browser_agent.py`, `cleanup_data.py` |
| `_applications/<folder_name>/` | the documents for one job | `batch_processor.py` |

`history.json` is the single source of truth for "already done". Removing a URL from it makes that job eligible for document generation again.

### Application status flow

```
pending ──approve──▶ approved ──execute──▶ submitted          confirmation detected
   │                                    ├─▶ awaiting_approval  form filled, not submitted
   │                                    ├─▶ needs_manual       automation blocked (see blocker)
   └──reject──▶ skipped                 └─▶ failed             technical error
```

Manual tracker statuses: `interview`, `offer`, `accepted`, `refused`, `withdrawn`, `dropped`.

### Server

FastAPI on `http://127.0.0.1:7788`, local only.

| Route | Purpose |
|---|---|
| `GET /` | dashboard |
| `GET /api/state` | metrics, review queue, all applications, log, running task |
| `POST /api/discover` | run job discovery |
| `POST /api/generate` | generate documents for in-scope targets not yet in history |
| `POST /api/execute` | run the browser agent on **approved** jobs |
| `POST /api/auto-cycle` | discover → generate → execute approved, three times |
| `POST /api/decision` | `{urls: [...], decision: "approve" \| "reject"}` |
| `POST /api/status` | `{url, status, note}` |
| `GET /applications/<folder>/<file>` | generated PDFs |

Only one pipeline task runs at a time; a second request returns HTTP 409.

---

## 2. Quick start

### Launch

Double-click `web-ui\Launch-WebUI.bat` (the desktop icon should point to it). It frees port 7788, starts `Job\api_server.py` with `web-ui\venv\Scripts\python.exe`, and opens the dashboard.

Manual start:

```
web-ui\venv\Scripts\python.exe Job\api_server.py --port 7788
```

Restart the server after changing any `.py` file. `index.html` is re-read on every page load.

### Normal workflow

1. **Discover Jobs** — adds new postings to `targets.json`.
2. **Generate Docs** — creates `CV.pdf` and `Anschreiben.pdf` for in-scope jobs (Werkstudent / Vollzeit, Germany).
3. **Review & PDF Preview** — open the PDFs, approve or reject each job.
4. **Execute Approved** — the browser agent works through approved jobs only.
5. **Application Tracker** — follow up and set statuses by hand.

### Execute settings (dashboard switches)

The three switches on the Control Center tab are stored in `Job\settings.json`. All are off by default.

| Switch | Effect |
|---|---|
| **Assist mode** | Visible browser with a remembered profile (`Job\_browser_profile\`). On a captcha, login or bot check the agent waits for you (amber banner in the dashboard and in the browser), then continues by itself. Needed for anything behind a captcha or login. |
| **Auto-submit** | Presses the final submit button once every gate has passed. Off: the form is filled and left (in assist mode the window stays open so you can press submit). |
| **Accept privacy consent** | Ticks required data-protection / terms checkboxes on application forms. Off: such forms stop as `needs_manual`. |
| **Auto-send e-mail applications** | Sends `Bewerbung.eml` from Gmail as soon as an employer's application address is known. Needs the app password in `Job\.env` (see below). |

Fully automatic run: all three on. The agent still stops and waits for you at a captcha or login; it never solves or bypasses those itself, and it never types a password.

### `application_answers.json`

Answers for fields beyond name, e-mail and phone: `salutation`, `street`, `country`, `birthdate`, `salary_werkstudent`, `salary_vollzeit`, `earliest_start`, `linkedin`, `github`, `website`, `message`. Empty values are never typed. If a form requires something that is empty here, the application stops as `needs_manual` and the Tracker names the field: add the answer and press Retry.

### Environment variables

Only first-run defaults and technical options; the switches above take precedence once set.

| Variable | Default | Effect |
|---|---|---|
| `JOB_APPLIER_AUTO_SUBMIT` | `0` | first-run default of the Auto-submit switch |
| `JOB_APPLIER_ASSIST` (or `JOB_APPLIER_HEADED`) | `0` | first-run default of the Assist mode switch |
| `JOB_APPLIER_ACCEPT_PRIVACY` | `0` | first-run default of the privacy-consent switch |
| `JOB_APPLIER_HUMAN_WAIT_S` | `300` | how long assist mode waits for you per captcha / login |
| `JOB_APPLIER_BROWSER_CHANNEL` | unset | e.g. `chrome` or `msedge` to use an installed browser instead of Playwright's Chromium |
| `JOB_APPLIER_BROWSER_PATH` | unset | explicit browser executable; overrides the channel |
| `JOB_APPLIER_MAX_JOBS` | `20` | maximum jobs processed per Generate run |
| `JOB_APPLIER_PAUSE_SECONDS` | `2` | pause between jobs during generation |

### E-mail applications

Many Bundesagentur postings show only the employer's application e-mail, behind the site's captcha. After you solve it in Assist mode, the agent writes `Bewerbung.eml` (recipient, subject, short text, Anschreiben.pdf + Lebenslauf.pdf) into the job folder.

To send these from Gmail:

1. On the Google account `malekaloui1777@gmail.com`, turn on 2-Step Verification, then create an app password at myaccount.google.com/apppasswords.
2. Paste it into `Job\.env` after `JOB_APPLIER_SMTP_PASSWORD=`. The normal account password does not work.
3. Check the login without sending anything: `python mailer.py --check`
4. Restart the server.

Then press **Send all ready e-mails** at the top of the Tracker (lists every recipient and asks for confirmation; works while Execute is running), **Send e-mail now** on a single row, or turn on the **Auto-send e-mail applications** switch so each draft goes out right after its captcha (20 s apart). Each job is sent at most once; a copy goes to the sending account (Bcc) and Gmail keeps it in the Sent folder. Sent jobs become `submitted`.

### Self-test

```
python selftest.py              all checks, in a temporary copy (real data untouched)
python selftest.py --no-browser skip the browser form scenarios
```

Run it after every code change. It must end with `0 failed`.

### `cleanup_data.py`

Run from `Job\`. Without `--apply` it only prints what it would do.

```
python cleanup_data.py                                show test/invalid records
python cleanup_data.py --apply                        remove them
python cleanup_data.py --reset-missing-docs           also list history entries whose PDFs are missing
python cleanup_data.py --reset-missing-docs --apply   drop those entries so Generate Docs rebuilds them
```

- Test/invalid means: fabricated StepStone ids (`TEST_ID_RANGES` in the script), placeholder company/title, URLs that are not job postings, duplicates in `targets.json`.
- `--reset-missing-docs` never touches an application that was already acted on (`submitted`, `interview`, `offer`, `accepted`, `refused`, `withdrawn`, `skipped`).
- Every write is atomic and backed up to `Job\_backup_cleanup\`.

### Other command-line entry points

```
python batch_processor.py --no-discover        generate documents only
python batch_processor.py --execute [--headed] [--approve]
python job_scraper.py --max-jobs 50
python apply_browser_agent.py <job_url> [--headed] [--approve]
```

### Browser-agent results

Each attempt prints one `[browser] RESULT {...}` JSON line and saves `apply_result.png` in the job's folder.

| Blocker code | Meaning |
|---|---|
| `captcha`, `bot_protection` | challenge or interstitial page; retry in Assist mode and clear it yourself |
| `login_required` | account or login needed; retry in Assist mode and log in once |
| `multi_step_form` | a step could not be advanced, or more than 8 steps |
| `no_form` | no name/e-mail fields plus an upload or next button found |
| `email_application` | the employer takes applications by e-mail; `Bewerbung.eml` is ready in the job folder |
| `posting_unavailable` | the posting is gone (status becomes `dropped`) |
| `required_fields_unfilled` | required inputs, checkboxes or uploads left empty |
| `no_submit_button` | no submit control belongs to the form |
| `validation_errors` | the site rejected the form after submit |
| `submission_unconfirmed` | submit was clicked but no confirmation appeared — check your inbox before applying again |

---

## 3. Mandatory AI reasoning and guardrails protocol

For any AI coding agent (OpenCode, Nemotron, Claude, or other) working in this workspace. These rules exist because earlier sessions patched symptoms repeatedly and left duplicated functions, fabricated test data and code that had never run.

### Rule 1 — Root-cause analysis first

- Do not patch code on the first turn of a non-trivial bug.
- Trace the full data flow first: the FastAPI response in `api_server.py` → the fields the dashboard reads in `static/index.html` → the JSON on disk (`history.json`, `targets.json`) → the module that wrote it.
- Write the diagnosis down before changing anything: what fails, where, why, and what else shares the cause.
- Read the whole function and its callers before editing it. Never paste a second copy of a function; replace the existing one.

### Rule 2 — Empirical proof over claims

- "This is fixed" is not acceptable without evidence. Run something and show the output.
- Minimum checks after a change:
  - `python -m py_compile <changed files>`
  - `GET /api/state` returns every key the dashboard reads (`metrics`, `review_queue`, `applications`, `status_labels`, `busy`, `auto_submit`, `log`), and `metrics` contains `tier1_count`, `tier2_count`, `status_breakdown`.
  - All three dashboard tabs render with data, with an empty history, and with the server stopped.
  - For agent changes: run it against local HTML test pages before any real site.
- State plainly what was not tested. Never report a simulated or assumed result as a real one.

### Rule 3 — Preserve system boundaries

- Pipeline code lives in `Job/`. `web-ui/` is a separate third-party project with its own git history; do not edit it except `Launch-WebUI.bat`.
- Use only `web-ui\venv` for this project. Do not install into system Python or another project's environment, and do not add `sys.path` entries pointing outside this workspace.
- Do not touch other folders on the Desktop (`AppScout`, `Studium`, and so on).
- Secrets stay in the `.env` files. Never copy keys into code, logs, documentation or chat.

### Project-specific safety rules

- **Never add captcha solving or bypassing** (solver services, token injection, clicking captcha widgets, stealth plugins) and never type passwords. Captchas and logins are handed to the person through Assist mode.
- **Never add "click anything that looks like a button" fallbacks** to the agent (class contains `btn`, text contains `senden` / `submit`, clicking every element in a section). They can press a submit button on an unverified page. `selftest.py` checks for this.
- **Do not replace `static/index.html` wholesale.** It reads `settings`, `attention`, `applications`, `blocker`; change it in place and run `selftest.py`.
- **Never weaken the approval gate.** Execute and Auto Cycle act only on `approved` jobs, and approval requires both PDFs on disk.
- **Never switch on Auto-submit, Assist mode or privacy consent on your own** (dashboard switches, `settings.json`, or the environment variables), and never run Execute against real employers without the user asking for it in that session.
- **Never submit a form the agent has not fully verified.** Keep every pre-submit gate in `apply_browser_agent.py`.
- **Never upload another job's documents.** Documents are resolved from the entry's own `folder_name` only.
- **Do not fabricate data.** No invented job URLs or ids in `targets.json` / `history.json`. Test against a copy of the folder or local fixtures.
- **Back up before rewriting** a state file or replacing a module, and write state files atomically.
- **Record what you changed** in `CHANGELOG.md`, including what remains unverified.
