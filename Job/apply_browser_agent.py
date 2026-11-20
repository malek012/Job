#!/usr/bin/env python3
"""
Job Applier Browser Agent (v3).

Drives a real browser (Playwright) through one job application:

  1. open the job URL, dismiss cookie banners / overlays, detect dead postings
  2. reach the application form: on-page form, the posting's "Bewerben / Apply"
     button, or the link to the employer's own application page
  3. detect the form with DOM heuristics (labels, names, ids, placeholders,
     autocomplete hints) in the page and its iframes
  4. fill contact data + the answers from application_answers.json, upload
     THIS job's CV and cover letter
  5. walk multi-step forms: fill a step, press "Weiter / Next", repeat
  6. submit only when every gate passes (auto_approve=True), else report the
     form as ready

Human hand-off (assist mode, visible browser)
    In assist mode the agent pauses at captchas, login walls or bot-protection
    pages, tells you what to do in the browser window, waits until you have
    cleared it, then carries on automatically (fill, next, submit). The browser
    profile is persistent, so a site you logged into once stays logged in.
    Without assist mode the same situations end as status "needs_manual".

It never submits an empty or partially filled form.

Result statuses
    submitted          form submitted and a confirmation was detected
    awaiting_approval  form filled completely; final click left to you
    needs_manual       automation stopped - see result["blocker"] / ["note"]
    error              technical failure (timeout, missing documents, crash)

Blocker codes (result["blocker"])
    captcha, bot_protection, login_required, multi_step_form, no_form,
    required_fields_unfilled, no_submit_button, validation_errors,
    submission_unconfirmed, posting_unavailable, email_application,
    browser_unavailable

Usage:
    python apply_browser_agent.py <job_url> [--headed] [--assist] [--approve] [--accept-privacy]

Environment:
    JOB_APPLIER_BROWSER_CHANNEL   e.g. "chrome" or "msedge" to use an installed browser
    JOB_APPLIER_BROWSER_PATH      explicit browser executable
    JOB_APPLIER_ACCEPT_PRIVACY=1  tick required privacy/consent checkboxes
    JOB_APPLIER_HUMAN_WAIT_S      seconds to wait for you in assist mode (default 300)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_JOB_DIR = Path(__file__).resolve().parent
if str(_JOB_DIR) not in sys.path:
    sys.path.insert(0, str(_JOB_DIR))

try:
    from playwright.sync_api import sync_playwright, Page, Frame, Locator
except ImportError as exc:
    print(f"FATAL: playwright not installed: {exc}")
    sys.exit(1)

HISTORY_PATH = _JOB_DIR / "history.json"
PROFILE_PATH = _JOB_DIR / "profile.json"
APPLICATIONS_DIR = _JOB_DIR / "_applications"

ACCEPT_PRIVACY = os.getenv("JOB_APPLIER_ACCEPT_PRIVACY", "0") == "1"
MANUAL_PREFIX = "Complex form detected - manual intervention required"
HEADED_MANUAL_WAIT_MS = 10 * 60 * 1000
HUMAN_WAIT_S = int(os.getenv("JOB_APPLIER_HUMAN_WAIT_S", "300"))
MAX_FORM_STEPS = 8
ANSWERS_PATH = _JOB_DIR / "application_answers.json"
PROFILE_DIR = _JOB_DIR / "_browser_profile"   # persistent logins/cookies for assist mode


# ── Candidate profile ───────────────────────────────────────────────────────

CANDIDATE = {
    "first_name": "Malek",
    "last_name": "Aloui",
    "email": "malekaloui1777@gmail.com",
    "phone": "+49 176 84243799",
    "location": "Dortmund 44263, Deutschland",
    "status": "B.Sc. Informatik abgeschlossen (TU Dortmund 2026)",
}


def load_candidate() -> Dict[str, str]:
    """CANDIDATE defaults, overridden by profile.json when present."""
    cand = dict(CANDIDATE)
    try:
        if PROFILE_PATH.exists():
            prof = json.loads(PROFILE_PATH.read_text(encoding="utf-8-sig"))
            name = str(prof.get("name", "")).strip()
            if name and " " in name:
                cand["first_name"], cand["last_name"] = name.split(" ", 1)
            for key in ("email", "phone", "location"):
                if prof.get(key):
                    cand[key] = str(prof[key])
    except Exception as exc:  # noqa: BLE001
        print(f"[browser] profile.json unreadable, using built-in candidate data: {exc}")
    cand["full_name"] = f"{cand['first_name']} {cand['last_name']}"
    zip_match = re.search(r"\b(\d{5})\b", cand.get("location", ""))
    cand["zip"] = zip_match.group(1) if zip_match else ""
    city = re.sub(r"\d{5}", "", cand.get("location", "").split(",")[0]).strip()
    cand["city"] = city
    cand.update(load_answers())
    return cand


# Extra answers for fields beyond name/e-mail/phone. Empty values are never
# typed anywhere; a required field without an answer stops as needs_manual.
DEFAULT_ANSWERS = {
    "salutation": "Herr",
    "street": "",
    "country": "Deutschland",
    "birthdate": "",
    "salary_werkstudent": "",
    "salary_vollzeit": "",
    "earliest_start": "ab sofort",
    "linkedin": "",
    "github": "https://github.com/malek012",
    "website": "",
    "message": "",
}


def load_answers() -> Dict[str, str]:
    """application_answers.json merged over the defaults (file is created on first use)."""
    answers = dict(DEFAULT_ANSWERS)
    try:
        if ANSWERS_PATH.exists():
            data = json.loads(ANSWERS_PATH.read_text(encoding="utf-8-sig"))
            answers.update({k: str(v) for k, v in data.items() if isinstance(v, (str, int, float))})
        else:
            ANSWERS_PATH.write_text(json.dumps(DEFAULT_ANSWERS, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"[browser] application_answers.json unreadable, using defaults: {exc}")
    return answers


def job_specific_answers(url: str, job_dir: Path, candidate: Dict[str, str]) -> Dict[str, str]:
    """Per-job values: salary by job type, and the cover-letter text for message boxes."""
    out = dict(candidate)
    entry = load_history().get(url, {}) if HISTORY_PATH.exists() else {}
    job_type = str(entry.get("job_type", "")).lower()
    out["salary"] = candidate.get("salary_werkstudent" if job_type == "werkstudent" else "salary_vollzeit", "")
    out["start_date"] = candidate.get("earliest_start", "")
    if not out.get("message"):
        try:
            payload = json.loads((job_dir / "cover_letter_payload.json").read_text(encoding="utf-8-sig"))
            parts = [payload.get("salutation", ""), payload.get("paragraph_1", ""), payload.get("paragraph_2", ""),
                     payload.get("paragraph_3", ""), "Mit freundlichen Grüßen", candidate.get("full_name", "")]
            out["message"] = "\n\n".join(p for p in parts if p)
        except Exception:  # noqa: BLE001
            pass
    out["_job_title"] = str(entry.get("job_title", ""))
    return out


# ── History helpers ────────────────────────────────────────────────────────

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


def record_submission(
    history: Dict[str, Any],
    url: str,
    status: str,
    note: str = "",
    company: str = "",
    job_title: str = "",
    blocker: Optional[str] = None,
) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = history.get(url, {})
    if company:
        entry["company"] = company
    if job_title:
        entry["job_title"] = job_title
    entry.update({
        "url": url,
        "application_status": status,
        "application_status_label": status.replace("_", " ").title(),
        "status_updated_at": now,
        "status_source": "browser_agent",
        "submission_note": note,
        "automation_blocker": blocker,
    })
    if status == "submitted":
        entry["submitted_at"] = now
    history[url] = entry
    save_history(history)


# ── Heuristic vocabulary ────────────────────────────────────────────────────

# (field_key, regex over the field's descriptor text). First match wins.
FIELD_RULES: List[Tuple[str, "re.Pattern[str]"]] = [
    ("email",      re.compile(r"e-?mail|courriel")),
    ("phone",      re.compile(r"phone|telefon|mobil|handy|\btel\b|rufnummer")),
    ("first_name", re.compile(r"first.?name|vorname|given.?name|\bfname\b|forename")),
    ("last_name",  re.compile(r"last.?name|nachname|surname|family.?name|\blname\b|familienname")),
    ("linkedin",   re.compile(r"linkedin")),
    ("github",     re.compile(r"github")),
    ("zip",        re.compile(r"\bzip\b|postal|\bplz\b|postleitzahl|postcode")),
    ("city",       re.compile(r"\bcity\b|\bort\b|\bstadt\b|wohnort")),
    ("street",     re.compile(r"stra(ß|ss)e|street|\baddress\b|\badresse\b|anschrift|hausnummer")),
    ("country",    re.compile(r"\bland\b|country|staat\b")),
    ("salary",     re.compile(r"gehalt|salary|verg(ü|ue)tung|compensation|verdienst")),
    ("start_date", re.compile(r"eintritt|start.?(datum|date|termin)|verf(ü|ue)gbar|availab|fr(ü|ue)hest|earliest")),
    ("birthdate",  re.compile(r"geburt|birth|\bdob\b")),
    ("website",    re.compile(r"website|webseite|homepage|portfolio")),
    ("full_name",  re.compile(r"full.?name|\bname\b|ihr name|your name")),
]
# Fields that are never part of an application's contact block
FIELD_SKIP = re.compile(
    r"search|suche|newsletter|passwor[dt]|captcha|user.?name|benutzername|login|"
    r"company|firma|unternehmen|coupon|gutschein|subscribe|abonnier"
)
INPUT_TYPES_FILLABLE = {"", "text", "email", "tel", "search", "url", "number"}
MESSAGE_RULE = re.compile(r"nachricht|message|anschreiben|cover.?letter|motivation|bemerkung|comment|mitteilung|"
                          r"about you|(ü|ue)ber (dich|sie)|warum|why")
SELECT_RULES: List[Tuple[str, "re.Pattern[str]"]] = [
    ("salutation", re.compile(r"anrede|salutation|gender|geschlecht")),
    ("country",    re.compile(r"\bland\b|country")),
]
# Option texts accepted for a stored answer (lower-case, prefix match)
SELECT_SYNONYMS = {
    "herr": ["herr", "mr", "male", "männlich", "mann"],
    "frau": ["frau", "ms", "mrs", "female", "weiblich"],
    "deutschland": ["deutschland", "germany", "de"],
}

FILE_CV = re.compile(r"\bcv\b|lebenslauf|resume|résumé|curriculum|vita")
FILE_LETTER = re.compile(r"anschreiben|cover|motivation|letter|bewerbungsschreiben")

APPLY_TRIGGER = re.compile(
    r"^\s*(jetzt\s+|direkt\s+|online\s+|hier\s+)*(bewerben|bewirb\s+dich)(\s+jetzt)?\s*$|"
    r"^\s*(zur\s+bewerbung|bewerbung\s+starten|ich\s+bin\s+interessiert)\s*$|"
    r"^\s*(apply(\s+now|\s+for\s+this\s+job|\s+online)?|easy\s+apply|i'?m\s+interested)\s*$|"
    r"^\s*(info(s|rmationen)?\s+zur\s+bewerbung|online[\s-]*bewerbung|zum\s+bewerbungsformular)\s*$",
    re.I,
)
SUBMIT_TEXT = re.compile(
    r"bewerbung\s+(ab)?senden|bewerbung\s+abschicken|absenden|abschicken|\bsenden\b|"
    r"\bsubmit\b|send\s+application|\bapply\b|\bbewerben\b",
    re.I,
)
NEXT_STEP_TEXT = re.compile(
    r"^\s*(weiter|n(ä|ae)chster?\s+schritt|fortfahren|next(\s+step)?|continue|"
    r"weiter\s+zu[mr]?\s+.+|save\s+(and|&)\s+continue|speichern\s+(und|&)\s+weiter)\s*$",
    re.I,
)
SUCCESS_TEXT = re.compile(
    r"vielen dank|herzlichen dank|danke f(ü|ue)r (deine|ihre) bewerbung|erfolgreich|"
    r"bewerbung (ist |wurde )?(eingegangen|versendet|gesendet|(ü|ue)bermittelt)|"
    r"thank you|thanks for applying|successfully|application (has been |was )?(received|submitted|sent)",
    re.I,
)
PRIVACY_TEXT = re.compile(r"datenschutz|privacy|einwillig|consent|\bagb\b|terms|nutzungsbedingungen|zustimm", re.I)
LOGIN_URL = re.compile(r"/(login|log-in|signin|sign-in|sso|auth|anmelden|anmeldung|register|registrieren)(/|\?|$)", re.I)

COOKIE_SELECTORS = [
    "#onetrust-accept-btn-handler",                                # OneTrust
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",      # Cookiebot
    "#CybotCookiebotDialogBodyButtonAccept",
    "[data-testid='uc-accept-all-button']",                        # Usercentrics (shadow DOM)
    "#ccmgt_explicit_accept",                                      # StepStone
    "#didomi-notice-agree-button",                                 # Didomi
    ".cmpboxbtnyes",                                               # consentmanager
    "#BorlabsCookieBox a._brlbs-btn-accept-all, .brlbs-btn-accept-all",  # Borlabs
    ".cky-btn-accept",                                             # CookieYes
    ".cc-allow, .cc-accept-all",                                   # Cookie Consent (Osano)
    "#cookie-accept-all, #accept-all-cookies, #acceptAllCookies, button#accept-all",
    "[aria-label='Alle akzeptieren'], [aria-label='Accept all']",
]
COOKIE_TEXT = re.compile(
    r"^\s*(alle\s+(cookies\s+)?(akzeptieren|annehmen|zulassen|erlauben)|"
    r"(cookies\s+)?akzeptieren(\s+und\s+(weiter|schlie(ß|ss)en))?|"
    r"accept(\s+all)?(\s+cookies)?|allow\s+all(\s+cookies)?|"
    r"zustimmen(\s+(und|&)\s+weiter)?|einverstanden|ich\s+stimme\s+zu|"
    r"einwilligen|akzeptieren\s+(und|&)\s+weiter|accept\s+(and|&)\s+continue|"
    r"i\s+agree|agree|got\s+it|verstanden|ok(ay)?)\s*$",
    re.I,
)

# JS: does this element sit inside something that looks like a consent layer?
_JS_IN_CONSENT_LAYER = """
el => {
  const pat = /cookie|consent|cmp|gdpr|dsgvo|privacy|datenschutz|onetrust|usercentrics|didomi|borlabs|banner/i;
  for (let n = el; n && n !== document.documentElement; n = n.parentElement || (n.getRootNode() && n.getRootNode().host)) {
    const sig = (n.id || '') + ' ' + (typeof n.className === 'string' ? n.className : '');
    if (pat.test(sig)) return true;
    const role = n.getAttribute && n.getAttribute('role');
    if ((role === 'dialog' || role === 'alertdialog') && /cookie|consent|datenschutz|privacy/i.test(n.innerText || '')) return true;
  }
  return false;
}
"""

# JS: everything the heuristics need to know about one form control
_JS_DESCRIBE = """
el => {
  const t = s => (s == null ? '' : String(s));
  let label = '';
  if (el.id) {
    const l = el.getRootNode().querySelector('label[for="' + CSS.escape(el.id) + '"]');
    if (l) label = l.innerText;
  }
  if (!label) { const l = el.closest('label'); if (l) label = l.innerText; }
  const lb = el.getAttribute('aria-labelledby');
  if (!label && lb) label = lb.split(/\\s+/).map(i => { const n = document.getElementById(i); return n ? n.innerText : ''; }).join(' ');
  if (!label && el.type === 'file') {
    const box = el.closest('[class*="upload" i], [class*="file" i], [class*="drop" i], fieldset, div');
    if (box) label = t(box.innerText).slice(0, 160);
  }
  const inSearch = !!el.closest('[role="search"], form[action*="search" i], form[action*="suche" i], form[class*="newsletter" i], form[id*="newsletter" i], [class*="newsletter" i]');
  return {
    tag: el.tagName.toLowerCase(),
    type: t(el.getAttribute('type')).toLowerCase(),
    name: t(el.getAttribute('name')), id: t(el.id),
    placeholder: t(el.getAttribute('placeholder')), aria: t(el.getAttribute('aria-label')),
    autocomplete: t(el.getAttribute('autocomplete')),
    label: t(label).replace(/\\s+/g, ' ').trim().slice(0, 160),
    required: !!el.required || el.getAttribute('aria-required') === 'true',
    value: t(el.value), checked: !!el.checked, multiple: !!el.multiple,
    disabled: !!el.disabled || !!el.readOnly,
    inSearch,
  };
}
"""


def _text_of(desc: Dict[str, Any]) -> str:
    return " ".join(
        str(desc.get(k, "")) for k in ("name", "id", "placeholder", "aria", "autocomplete", "label")
    ).lower()


def _label_of(desc: Dict[str, Any]) -> str:
    return (desc.get("label") or desc.get("aria") or desc.get("placeholder") or desc.get("name") or desc.get("id") or "?")[:60]


def _frames(page: Page) -> List[Frame]:
    return [f for f in page.frames if not f.is_detached()]


def _visible(loc: Locator) -> bool:
    try:
        return loc.is_visible()
    except Exception:  # noqa: BLE001
        return False


def _log(msg: str) -> None:
    try:
        print(f"[browser] {msg}", flush=True)
    except UnicodeEncodeError:
        print(f"[browser] {msg}".encode("ascii", "replace").decode("ascii"), flush=True)


def _inspect_log(ctx: Dict[str, Any], msg: str) -> None:
    """Verbose log in inspect mode."""
    if ctx.get("inspect"):
        _log(f"[INSPECT] {msg}")
        ctx["emit"](f"[INSPECT] {msg}")


def _inspect_pause(ctx: Dict[str, Any], step: str) -> None:
    """Pause in inspect mode, waiting for Enter to continue."""
    if ctx.get("inspect"):
        _log(f"[INSPECT] PAUSED at: {step} — press Enter in terminal to continue...")
        try:
            input()
        except EOFError:
            pass
        _log(f"[INSPECT] Continuing after: {step}")


def _inspect_form(ctx: Dict[str, Any], form: Dict[str, Any], label: str) -> None:
    """Log detailed form detection results in inspect mode."""
    if not ctx.get("inspect"):
        return
    fields = [(f["key"], _label_of(f["desc"])) for f in form["fields"]]
    files = [(f["kind"], _label_of(f["desc"])) for f in form["files"]]
    _inspect_log(ctx, f"{label} — fields: {fields or 'none'} | files: {files or 'none'} | frame: {form['frame'].url[:80]}")
    _inspect_pause(ctx, f"{label} (form scan)")


# ── 1. Cookie banners & overlays ────────────────────────────────────────────

def dismiss_overlays(page: Page) -> List[str]:
    """Accept cookie banners and close blocking overlays. Returns what was clicked."""
    clicked: List[str] = []
    for _round in range(2):  # some sites stack a second layer behind the first
        hit = False
        for frame in _frames(page):
            for sel in COOKIE_SELECTORS:
                try:
                    loc = frame.locator(sel).first
                    if _visible(loc):
                        loc.click(timeout=2000)
                        clicked.append(f"cookie:{sel}")
                        hit = True
                        break
                except Exception:  # noqa: BLE001
                    continue
            if hit:
                break
            # Text-based fallback, restricted to elements inside a consent layer
            try:
                candidates = frame.locator("button, [role='button'], a, input[type='button'], input[type='submit']")
                for el in candidates.filter(has_text=COOKIE_TEXT).all()[:8]:
                    if not _visible(el):
                        continue
                    if not el.evaluate(_JS_IN_CONSENT_LAYER):
                        continue
                    label = (el.inner_text() or "").strip()[:40]
                    el.click(timeout=2000)
                    clicked.append(f"cookie-text:{label}")
                    hit = True
                    break
            except Exception:  # noqa: BLE001
                pass
            if hit:
                break
        if not hit:
            break
        page.wait_for_timeout(600)

    # Generic modal close buttons (newsletter pop-ups etc.) that do not hold the form
    try:
        for dlg in page.locator("[role='dialog'], [aria-modal='true'], .modal.show").all()[:3]:
            if not _visible(dlg) or dlg.locator("input[type='file']").count():
                continue
            close = dlg.locator(
                "[aria-label*='close' i], [aria-label*='schlie' i], button.close, .btn-close, [data-dismiss='modal']"
            ).first
            if _visible(close):
                close.click(timeout=1500)
                clicked.append("overlay:close")
                page.wait_for_timeout(300)
    except Exception:  # noqa: BLE001
        pass
    return clicked


# ── 2. Form detection ───────────────────────────────────────────────────────

def classify_field(desc: Dict[str, Any]) -> Optional[str]:
    """Map one control (input / textarea / select) to an answer key, or None."""
    if desc["disabled"] or desc["inSearch"]:
        return None
    tag, typ = desc["tag"], desc["type"]
    text = _text_of(desc)
    if FIELD_SKIP.search(text):
        return None
    if tag == "textarea":
        return "message" if MESSAGE_RULE.search(text) else None
    if tag == "select":
        for key, pattern in SELECT_RULES:
            if pattern.search(text):
                return key
        return None
    if tag != "input" or typ not in INPUT_TYPES_FILLABLE:
        return None
    if typ == "email":
        return "email"
    if typ == "tel":
        return "phone"
    for key, pattern in FIELD_RULES:
        if pattern.search(text):
            return key
    return None


def scan_form(page: Page) -> Dict[str, Any]:
    """
    Inspect every frame and return the detected application form:
      fields:  [{key, locator, desc}]   controls we know how to fill
      files:   [{kind, locator, desc}]  file inputs (kind: cv | letter | other)
      frame:   the frame holding the form (the one with the most evidence)
    """
    best: Dict[str, Any] = {"fields": [], "files": [], "frame": page.main_frame, "score": 0}
    for frame in _frames(page):
        fields: List[Dict[str, Any]] = []
        files: List[Dict[str, Any]] = []
        try:
            controls = frame.locator("input, textarea, select").all()
        except Exception:  # noqa: BLE001
            continue
        seen_keys: set = set()
        for el in controls[:250]:
            try:
                desc = el.evaluate(_JS_DESCRIBE)
            except Exception:  # noqa: BLE001
                continue
            if desc["tag"] == "input" and desc["type"] == "file":
                if desc["disabled"] or desc["inSearch"]:
                    continue
                text = _text_of(desc)
                kind = "cv" if FILE_CV.search(text) else "letter" if FILE_LETTER.search(text) else "other"
                files.append({"kind": kind, "locator": el, "desc": desc})
                continue
            if not _visible(el):
                continue
            key = classify_field(desc)
            if key and key not in seen_keys:
                seen_keys.add(key)
                fields.append({"key": key, "locator": el, "desc": desc})
        score = len(fields) + 3 * len(files)
        if score > best["score"]:
            best = {"fields": fields, "files": files, "frame": frame, "score": score}
    return best


def _has_identity(form: Dict[str, Any]) -> bool:
    keys = {f["key"] for f in form["fields"]}
    return "email" in keys or {"first_name", "last_name"} <= keys or "full_name" in keys


def looks_like_application_form(form: Dict[str, Any]) -> bool:
    """A single-page application form: a document upload plus applicant identity."""
    return bool(form["files"]) and _has_identity(form)


def is_form_start(form: Dict[str, Any]) -> bool:
    """First page of an application: identity fields plus an upload or a 'next' button."""
    if looks_like_application_form(form):
        return True
    return _has_identity(form) and len(form["fields"]) >= 2 and find_next_button(form) is not None


# ── 3. Reaching the form ────────────────────────────────────────────────────

AGGREGATOR_HOSTS = ("arbeitsagentur.de", "stepstone.de", "arbeitnow.com", "interamt.de",
                    "finest-jobs.com", "stellenwerk.de", "indeed.", "linkedin.com", "xing.com")
EXTERNAL_APPLY_TEXT = re.compile(
    r"company\s+(portal|site|website|page)|apply\s+on\s+(the\s+)?company|arbeitgeber(seite|-?webseite|-?website)|"
    r"zur\s+(karriereseite|stellenanzeige|bewerbung|website|webseite)|extern(e|en)?\s+(seite|bewerbung|link)|"
    r"bewerbungsportal|karriereportal|online[\s-]*bewerbung|direkt\s+(beim|bei)\s+arbeitgeber",
    re.I,
)
UNAVAILABLE_TEXT = re.compile(
    r"fehler 404|error 404|seite nicht gefunden|page not found|nicht mehr verf(ü|ue)gbar|no longer available|"
    r"stelle(nangebot)? (ist |wurde )?(nicht mehr|bereits besetzt|abgelaufen|deaktiviert)|"
    r"this job (is|has) (no longer|expired|been filled)|position has been filled|anzeige (ist )?abgelaufen",
    re.I,
)
SAFE_EXPAND_TEXT = re.compile(r"upload|datei|anhang|anh(ä|ae)nge|lebenslauf|anschreiben|dokument|unterlagen|attachment|resume|\bcv\b", re.I)


def _host(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return urlparse(url).netloc.lower()
    except Exception:  # noqa: BLE001
        return ""


def _btn_text(el: Locator) -> str:
    try:
        return ((el.inner_text() or "") or (el.get_attribute("value") or "")).strip()
    except Exception:  # noqa: BLE001
        return ""


def find_external_apply_link(page: Page) -> Optional[str]:
    """
    On a job-board page: the link to the employer's own application page.
    Only real <a href> links to a different site whose text says so. Nothing
    is clicked here, so this can never trigger a submit.
    """
    from urllib.parse import urljoin
    here = _host(page.url)
    for frame in _frames(page):
        try:
            anchors = frame.locator("a[href]").all()[:200]
        except Exception:  # noqa: BLE001
            continue
        for a in anchors:
            try:
                if not _visible(a):
                    continue
                href = urljoin(page.url, a.get_attribute("href") or "")
                if not href.startswith("http"):
                    continue
                target = _host(href)
                if not target or target == here or any(h in target for h in AGGREGATOR_HOSTS):
                    continue
                if EXTERNAL_APPLY_TEXT.search((a.inner_text() or "").strip()):
                    return href
            except Exception:  # noqa: BLE001
                continue
    return None


def expand_hidden_sections(page: Page) -> int:
    """
    Reveal collapsed upload sections: open <details> and press collapsed
    toggles (aria-expanded="false") whose label is about uploads/documents.
    Never touches anything that reads like submit / next / apply.
    """
    opened = 0
    for frame in _frames(page):
        try:
            opened += frame.evaluate(
                "() => { let n = 0; document.querySelectorAll('form details:not([open])').forEach(d => { d.open = true; n++; }); return n; }"
            )
            for el in frame.locator("[aria-expanded='false']").all()[:20]:
                text = _btn_text(el)
                if (_visible(el) and SAFE_EXPAND_TEXT.search(text) and not SUBMIT_TEXT.search(text)
                        and not NEXT_STEP_TEXT.search(text) and not APPLY_TRIGGER.search(text)):
                    el.click(timeout=2000)
                    opened += 1
        except Exception:  # noqa: BLE001
            continue
    if opened:
        page.wait_for_timeout(500)
    return opened


def open_application_form(page: Page, steps: List[str], clicked: Optional[set] = None) -> Page:
    """
    Get from a job posting to its application form, at most three hops:
      a) the form is already on this page -> done
      b) press the posting's apply button (same tab or new tab)
      c) follow the link to the employer's own application page
    Returns the page to work on.
    """
    clicked = clicked if clicked is not None else set()
    for _hop in range(3):
        expand_hidden_sections(page)
        if is_form_start(scan_form(page)):
            return page

        trigger: Optional[Locator] = None
        label = ""
        for frame in _frames(page):
            try:
                for el in frame.locator("a, button, [role='button'], input[type='button']").filter(has_text=APPLY_TRIGGER).all()[:8]:
                    text = _btn_text(el)[:40]
                    if _visible(el) and (page.url, text.lower()) not in clicked:
                        trigger, label = el, text
                        break
            except Exception:  # noqa: BLE001
                continue
            if trigger:
                break

        if trigger is not None:
            clicked.add((page.url, label.lower()))
            pages_before = len(page.context.pages)
            try:
                trigger.click(timeout=4000)
            except Exception as exc:  # noqa: BLE001
                steps.append(f"apply button '{label}' not clickable: {str(exc)[:80]}")
                return page
            page.wait_for_timeout(1500)
            if len(page.context.pages) > pages_before:      # opened in a new tab
                page = page.context.pages[-1]
            steps.append(f"pressed '{label}' -> {page.url[:100]}")
        else:
            external = find_external_apply_link(page)
            if not external or external in clicked:
                return page
            clicked.add(external)
            try:
                page.goto(external, wait_until="domcontentloaded", timeout=20000)
            except Exception as exc:  # noqa: BLE001
                steps.append(f"employer page did not load: {str(exc)[:80]}")
                return page
            steps.append(f"followed employer link -> {page.url[:100]}")

        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(1000)
        steps.extend(dismiss_overlays(page))
    return page


# ── 4. Things only a human may clear ────────────────────────────────────────

_JS_CAPTCHA_SOLVED = """
() => {
  const tokens = document.querySelectorAll(
    'textarea[name="g-recaptcha-response"], [name="h-captcha-response"], [name="cf-turnstile-response"]');
  for (const t of tokens) if ((t.value || '').length > 20) return true;
  for (const i of document.querySelectorAll('input[name*="captcha" i]:not([type="hidden"])'))
    if ((i.value || '').trim().length >= 3) return true;
  return false;
}
"""
CAPTCHA_WIDGETS = (
    "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[src*='challenges.cloudflare.com'], "
    "iframe[src*='captcha'], .g-recaptcha, .h-captcha, .cf-turnstile, #captcha, [class*='captcha' i], "
    "img[src*='captcha' i], input[name*='captcha' i]:not([type='hidden'])"
)


def captcha_state(page: Page) -> str:
    """'none' | 'unsolved' | 'solved'. Read-only: the agent never interacts with a captcha."""
    present = False
    for frame in _frames(page):
        try:
            for el in frame.locator(CAPTCHA_WIDGETS).all()[:10]:
                if not _visible(el) or el.evaluate("e => !!e.closest('.grecaptcha-badge')"):
                    continue   # the small invisible-reCAPTCHA badge is not a challenge
                box = el.bounding_box()
                if box and box["width"] >= 60 and box["height"] >= 30:
                    present = True
                    break
        except Exception:  # noqa: BLE001
            continue
    if not present:
        return "none"
    for frame in _frames(page):
        try:
            if frame.evaluate(_JS_CAPTCHA_SOLVED):
                return "solved"
        except Exception:  # noqa: BLE001
            continue
    return "unsolved"


def human_blocker(page: Page, form: Dict[str, Any], before_send: bool = False) -> Optional[Tuple[str, str, str]]:
    """
    (code, explanation, instruction for the person) when only a human may proceed.
    A captcha next to a fillable form is not a blocker until just before data is
    sent (before_send=True), so the agent can fill everything first.
    """
    try:
        title = (page.title() or "").lower()
    except Exception:  # noqa: BLE001
        title = ""
    if re.search(r"just a moment|attention required|access denied|zugriff verweigert|are you a robot|sind sie ein mensch", title):
        return ("bot_protection", f"bot-protection page ('{title[:40]}')",
                "Complete the site's check in the browser window")

    form_ready = is_form_start(form) or before_send
    if captcha_state(page) == "unsolved" and (before_send or not form_ready):
        return ("captcha", "captcha challenge on the page", "Solve the captcha in the browser window")

    has_password = False
    for frame in _frames(page):
        try:
            if any(_visible(p) for p in frame.locator("input[type='password']").all()[:3]):
                has_password = True
                break
        except Exception:  # noqa: BLE001
            continue
    if (has_password and not form["files"]) or (not form_ready and LOGIN_URL.search(page.url)):
        return ("login_required", "login or account registration required before applying",
                "Log in (or register) in the browser window")
    return None


def wait_for_human(page: Page, ctx: Dict[str, Any], blocker: Tuple[str, str, str], before_send: bool) -> bool:
    """
    Assist mode: show the person what to do, then poll until they have done it.
    Returns True when the blocker is gone. The agent only watches.
    """
    code, why, todo = blocker
    wait_s = ctx["human_wait_s"]
    ctx["emit"](f"ACTION NEEDED: {todo}. The application continues automatically afterwards "
                f"(waiting up to {wait_s // 60} min {wait_s % 60} s).")
    banner_js = """
    (msg) => { let b = document.getElementById('__jobapplier_banner');
      if (!b) { b = document.createElement('div'); b.id = '__jobapplier_banner';
        b.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#f59e0b;color:#111;' +
          'font:600 14px/1.4 sans-serif;padding:8px 14px;text-align:center;pointer-events:none';
        document.documentElement.appendChild(b); }
      b.textContent = msg; }
    """
    deadline = wait_s * 1000
    waited = 0
    while waited < deadline:
        try:
            if page.is_closed():
                return False
            page.evaluate(banner_js, f"Job Applier is waiting for you: {todo}. It continues automatically.")
        except Exception:  # noqa: BLE001  (navigation in progress)
            pass
        page.wait_for_timeout(2000)
        waited += 2000
        try:
            if human_blocker(page, scan_form(page), before_send) is None:
                try:
                    page.evaluate("() => { const b = document.getElementById('__jobapplier_banner'); if (b) b.remove(); }")
                except Exception:  # noqa: BLE001
                    pass
                ctx["emit"](f"Thanks - {code.replace('_', ' ')} cleared, continuing.")
                return True
        except Exception:  # noqa: BLE001
            continue
    ctx["emit"](f"Gave up waiting for: {todo}")
    return False


# ── 5. Filling, uploading, navigating steps ─────────────────────────────────

def _select_option(el: Locator, answer: str) -> bool:
    wanted = SELECT_SYNONYMS.get(answer.strip().lower(), [answer.strip().lower()])
    value = el.evaluate(
        """(el, wanted) => { for (const o of el.options) { const t = (o.text || '').trim().toLowerCase();
             if (t && wanted.some(w => t === w || t.startsWith(w + ' ') || t.startsWith(w + '.'))) return o.value; }
             return null; }""",
        wanted,
    )
    if value is None:
        return False
    el.select_option(value=value, timeout=3000)
    return True


def fill_form_fields(form: Dict[str, Any], answers: Dict[str, str]) -> Dict[str, bool]:
    results: Dict[str, bool] = {}
    for field in form["fields"]:
        key, desc, el = field["key"], field["desc"], field["locator"]
        value = answers.get(key, "")
        if not value:
            continue                     # no answer on file: leave it for the required-field check
        try:
            if desc["tag"] == "select":
                results[key] = _select_option(el, value)
                continue
            if desc["value"].strip():    # pre-filled by the site (e.g. logged-in profile)
                results[key] = True
                continue
            if desc["type"] == "number":
                value = re.sub(r"\D", "", value)
                if not value:
                    continue
            el.fill(value, timeout=3000)
            results[key] = True
        except Exception:  # noqa: BLE001
            results[key] = False
    return results


def upload_documents(form: Dict[str, Any], cv_path: Path, letter_path: Path, done: Dict[str, bool]) -> None:
    """Route CV and cover letter to the right file inputs (hidden inputs included). Updates `done`."""
    files = form["files"]
    if not files:
        return

    def pick(kind: str, exclude: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        for f in files:
            if f["kind"] == kind and f is not exclude:
                return f
        return None

    cv_input = None if done["cv"] else (pick("cv") or pick("other"))
    letter_input = None if done["letter"] else (pick("letter") or pick("other", exclude=cv_input))
    try:
        if cv_input and letter_input is None and not done["letter"] and cv_input["desc"]["multiple"]:
            cv_input["locator"].set_input_files([str(cv_path), str(letter_path)])
            done["cv"] = done["letter"] = True
            return
        if cv_input:
            cv_input["locator"].set_input_files(str(cv_path))
            done["cv"] = True
        if letter_input:
            letter_input["locator"].set_input_files(str(letter_path))
            done["letter"] = True
    except Exception as exc:  # noqa: BLE001
        _log(f"upload error: {str(exc)[:120]}")


def _form_root(form: Dict[str, Any]) -> Any:
    """The <form> that owns the upload (or first field); the whole frame for JS-built forms."""
    frame: Frame = form["frame"]
    try:
        owning = frame.locator("form").filter(has=frame.locator("input[type='file']"))
        if owning.count():
            return owning.first
        if form["fields"]:
            anchor = form["fields"][0]["locator"].locator("xpath=ancestor::form[1]")
            if anchor.count():
                return anchor.first
    except Exception:  # noqa: BLE001
        pass
    return frame


def required_unfilled(form: Dict[str, Any], accept_privacy: bool) -> List[str]:
    """Labels of required controls in the application form that are still empty."""
    root = _form_root(form)
    missing: List[str] = []
    try:
        controls = root.locator("input, textarea, select").all()[:250]
    except Exception:  # noqa: BLE001
        return missing
    radio_groups: Dict[str, bool] = {}
    for el in controls:
        try:
            desc = el.evaluate(_JS_DESCRIBE)
        except Exception:  # noqa: BLE001
            continue
        if not desc["required"] or desc["disabled"] or desc["inSearch"]:
            continue
        typ = desc["type"]
        if typ in ("hidden", "submit", "button", "image", "reset"):
            continue
        if typ == "file":
            if not el.evaluate("e => e.files && e.files.length > 0"):
                missing.append(f"upload: {_label_of(desc)}")
            continue
        if not _visible(el) and typ not in ("checkbox", "radio"):
            continue
        if typ == "checkbox":
            if desc["checked"]:
                continue
            if accept_privacy and PRIVACY_TEXT.search(_text_of(desc)):
                try:
                    el.check(timeout=2000, force=True)
                    continue
                except Exception:  # noqa: BLE001
                    pass
            missing.append(f"checkbox: {_label_of(desc)}")
        elif typ == "radio":
            name = desc["name"] or desc["id"]
            radio_groups[name] = radio_groups.get(name, False) or desc["checked"]
        elif not desc["value"].strip():
            missing.append(_label_of(desc))
    missing.extend(f"choice: {name}" for name, ok in radio_groups.items() if not ok)
    return missing


def _buttons(form: Dict[str, Any]) -> List[Locator]:
    root = _form_root(form)
    try:
        found = root.locator("button, [role='button'], input[type='submit'], input[type='button']").all()[:40]
    except Exception:  # noqa: BLE001
        return []
    out = []
    for el in found:
        try:
            if _visible(el) and el.is_enabled() and not el.evaluate(
                "e => !!e.closest('[role=\\'search\\'], [class*=\\'newsletter\\' i], form[action*=\\'search\\' i]')"
            ):
                out.append(el)
        except Exception:  # noqa: BLE001
            continue
    return out


def find_next_button(form: Dict[str, Any]) -> Optional[Locator]:
    """'Weiter / Next / Continue' control of a multi-step form."""
    for el in _buttons(form):
        if NEXT_STEP_TEXT.search(_btn_text(el)):
            return el
    return None


def find_submit_button(form: Dict[str, Any]) -> Optional[Locator]:
    """Final submit control that belongs to the application form (never a page-wide guess)."""
    candidates = [el for el in _buttons(form) if not NEXT_STEP_TEXT.search(_btn_text(el))]
    for el in candidates:                       # explicit wording first
        if SUBMIT_TEXT.search(_btn_text(el)):
            return el
    if _form_root(form) is not form["frame"]:   # inside a real <form>: its submit button
        for el in candidates:
            try:
                if el.evaluate("e => (e.getAttribute('type') || (e.tagName === 'BUTTON' ? 'submit' : '')).toLowerCase() === 'submit'"):
                    return el
            except Exception:  # noqa: BLE001
                continue
    return None


def validation_error(page: Page) -> Optional[str]:
    try:
        for frame in _frames(page):
            invalid = frame.locator("[aria-invalid='true'], .invalid-feedback, .field-error, .form-error, .error-message, .has-error")
            shown = [e for e in invalid.all()[:10] if _visible(e)]
            if shown:
                return (shown[0].inner_text() or "").strip()[:100] or "field marked invalid"
    except Exception:  # noqa: BLE001
        pass
    return None


def step_signature(page: Page) -> str:
    """Fingerprint of the current form step, to tell whether 'next' really advanced."""
    try:
        return page.evaluate(
            """() => location.href + '|' + [...document.querySelectorAll('input,textarea,select,h1,h2,h3,legend')]
                 .filter(e => e.offsetParent !== null)
                 .map(e => e.tagName + ':' + (e.name || e.id || (e.innerText || '').slice(0, 30))).join(',')"""
        )
    except Exception:  # noqa: BLE001
        return page.url


def verify_submission(page: Page) -> Tuple[bool, str]:
    """After clicking submit: (confirmed, explanation)."""
    page.wait_for_timeout(3500)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:  # noqa: BLE001
        pass
    err = validation_error(page)
    if err:
        return False, f"validation errors after submit ({err})"
    try:
        body = page.inner_text("body", timeout=3000)
    except Exception:  # noqa: BLE001
        body = ""
    m = SUCCESS_TEXT.search(body)
    if m:
        return True, f"confirmation text found: '{m.group(0)[:50]}'"
    return False, "no confirmation message detected after clicking submit"


# ── 6. Applications by e-mail ───────────────────────────────────────────────

EMAIL_PREFERRED = re.compile(r"bewerb|job|karriere|career|recruit|personal|\bhr\b|talent|apply", re.I)
EMAIL_IGNORED = re.compile(r"no-?reply|datenschutz|privacy|presse|press@|support@|abuse|webmaster|impressum", re.I)


def find_application_email(page: Page) -> Optional[str]:
    """An e-mail address the posting gives for applications (mailto links only)."""
    found: List[str] = []
    for frame in _frames(page):
        try:
            for a in frame.locator("a[href^='mailto:']").all()[:30]:
                addr = (a.get_attribute("href") or "")[7:].split("?")[0].strip()
                if re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", addr, re.I) and not EMAIL_IGNORED.search(addr) and addr not in found:
                    found.append(addr)
        except Exception:  # noqa: BLE001
            continue
    for addr in found:
        if EMAIL_PREFERRED.search(addr):
            return addr
    return found[0] if len(found) == 1 else None


def write_email_draft(job_dir: Path, to_addr: str, answers: Dict[str, str], cv_path: Path, letter_path: Path) -> Path:
    """Ready-to-send draft (Bewerbung.eml) with both PDFs attached. Opening it shows a normal compose window."""
    from email.message import EmailMessage
    msg = EmailMessage()
    title = answers.get("_job_title") or "die ausgeschriebene Stelle"
    msg["To"] = to_addr
    msg["From"] = f"{answers.get('full_name', '')} <{answers.get('email', '')}>"
    msg["Subject"] = f"Bewerbung als {title}"
    msg["X-Unsent"] = "1"
    msg.set_content(
        "Sehr geehrte Damen und Herren,\n\n"
        f"anbei erhalten Sie meine Bewerbungsunterlagen für die Stelle \"{title}\" "
        "(Anschreiben und Lebenslauf als PDF).\n\n"
        "Über eine Rückmeldung freue ich mich sehr.\n\n"
        f"Mit freundlichen Grüßen\n{answers.get('full_name', '')}\n{answers.get('phone', '')}\n"
    )
    for path, name in ((letter_path, "Anschreiben.pdf"), (cv_path, "Lebenslauf.pdf")):
        msg.add_attachment(path.read_bytes(), maintype="application", subtype="pdf", filename=name)
    out = job_dir / "Bewerbung.eml"
    out.write_bytes(bytes(msg))
    return out


# ── Main application flow ───────────────────────────────────────────────────

def _manual(result: Dict[str, Any], code: str, why: str) -> None:
    result["status"] = "needs_manual"
    result["blocker"] = code
    result["note"] = f"{MANUAL_PREFIX}: {why}"


def _open_browser(p: Any, headless: bool, persistent: bool) -> Tuple[Any, Any]:
    """Returns (closable, context). Assist mode uses a persistent profile so logins survive."""
    kwargs: Dict[str, Any] = {"headless": headless}
    if os.getenv("JOB_APPLIER_BROWSER_PATH"):
        kwargs["executable_path"] = os.environ["JOB_APPLIER_BROWSER_PATH"]
    elif os.getenv("JOB_APPLIER_BROWSER_CHANNEL"):
        kwargs["channel"] = os.environ["JOB_APPLIER_BROWSER_CHANNEL"]
    view = {"viewport": {"width": 1280, "height": 900}, "locale": "de-DE"}
    if persistent:
        PROFILE_DIR.mkdir(exist_ok=True)
        context = p.chromium.launch_persistent_context(str(PROFILE_DIR), **kwargs, **view)
        return context, context
    browser = p.chromium.launch(**kwargs)
    return browser, browser.new_context(**view)


def _handoff(page: Page, ctx: Dict[str, Any], result: Dict[str, Any], blocker: Tuple[str, str, str], before_send: bool) -> bool:
    """True = cleared by the person, carry on. False = result has been set to needs_manual."""
    if ctx["assist"] and wait_for_human(page, ctx, blocker, before_send):
        result["steps"].append(f"{blocker[0]} cleared by you")
        return True
    hint = "" if ctx["assist"] else " (turn on Assist mode to clear this yourself and let the agent continue)"
    _manual(result, blocker[0], blocker[1] + hint)
    return False


def _run(page: Page, ctx: Dict[str, Any], result: Dict[str, Any], response_status: Optional[int]) -> Page:
    steps: List[str] = result["steps"]
    answers, cv_path, letter_path = ctx["answers"], ctx["cv_path"], ctx["letter_path"]

    # Dead posting?
    try:
        head_text = (page.title() or "") + " " + page.inner_text("body", timeout=3000)[:1500]
    except Exception:  # noqa: BLE001
        head_text = ""
    if (response_status or 200) in (404, 410) or UNAVAILABLE_TEXT.search(head_text):
        _manual(result, "posting_unavailable", f"the posting is gone (HTTP {response_status or '?'} / 'not found' page)")
        return page

    steps.extend(dismiss_overlays(page))
    clicked: set = set()
    page = open_application_form(page, steps, clicked)

    filled: Dict[str, bool] = {}
    uploads = {"cv": False, "letter": False}
    submit_btn: Optional[Locator] = None

    for step_no in range(1, MAX_FORM_STEPS + 1):
        form = scan_form(page)
        _inspect_form(ctx, form, f"step {step_no} form scan")

        blocker = human_blocker(page, form)
        if blocker:
            if not _handoff(page, ctx, result, blocker, before_send=False):
                return page
            page = page.context.pages[-1] if page.is_closed() else page
            steps.extend(dismiss_overlays(page))
            page = open_application_form(page, steps, clicked)
            form = scan_form(page)

        if step_no == 1 and not is_form_start(form):
            email = find_application_email(page)
            if email:
                draft = write_email_draft(cv_path.parent, email, answers, cv_path, letter_path)
                result["apply_email"] = email
                _manual(result, "email_application",
                        f"this employer takes applications by e-mail ({email}); a ready-to-send draft with both PDFs "
                        f"is saved as {draft.name} in the job folder")
            else:
                found = sorted({f["key"] for f in form["fields"]})
                _manual(result, "no_form", "no application form found (needs name/e-mail fields plus a document upload "
                                           f"or a next button; saw fields={found or 'none'}, uploads={len(form['files'])})")
            return page

        expand_hidden_sections(page)
        filled.update(fill_form_fields(form, answers))
        _inspect_log(ctx, f"step {step_no} filled: {sorted(k for k, v in filled.items() if v)}")
        _inspect_pause(ctx, f"step {step_no} after fill")
        upload_documents(form, cv_path, letter_path, uploads)
        _inspect_log(ctx, f"step {step_no} uploads: cv={uploads['cv']} letter={uploads['letter']}")
        _inspect_pause(ctx, f"step {step_no} after upload")
        result["filled_fields"], result["uploaded_docs"] = dict(filled), dict(uploads)

        missing = required_unfilled(form, ctx["accept_privacy"])
        result["required_unfilled"] = missing
        if missing:
            shown = "; ".join(missing[:6]) + (" ..." if len(missing) > 6 else "")
            where = f" on step {step_no}" if step_no > 1 else ""
            _manual(result, "required_fields_unfilled",
                    f"{len(missing)} required field(s) left empty{where}: {shown} "
                    "(add answers to application_answers.json, or enable the privacy-consent setting for consent boxes)")
            return page

        next_btn, submit_btn = find_next_button(form), find_submit_button(form)
        _inspect_log(ctx, f"step {step_no} next_btn={_btn_text(next_btn)[:30] if next_btn else 'none'} submit_btn={_btn_text(submit_btn)[:30] if submit_btn else 'none'}")
        _inspect_pause(ctx, f"step {step_no} before next/submit")
        if submit_btn is not None:
            steps.append(f"step {step_no}: final step reached" if step_no > 1 else "single-page form")
            break
        if next_btn is None:
            code = "no_submit_button" if step_no == 1 else "multi_step_form"
            _manual(result, code, f"step {step_no} has neither a submit nor a next button that belongs to the form")
            return page

        # Intermediate step: a captcha must be cleared by the person before data is sent on
        blocker = human_blocker(page, form, before_send=True)
        if blocker and not _handoff(page, ctx, result, blocker, before_send=True):
            return page
        before = step_signature(page)
        label = _btn_text(next_btn)[:30]
        next_btn.click(timeout=5000)
        page.wait_for_timeout(1800)
        steps.extend(dismiss_overlays(page))
        err = validation_error(page)
        if err:
            _manual(result, "validation_errors", f"step {step_no} was rejected by the site: {err}")
            return page
        if step_signature(page) == before:
            _manual(result, "multi_step_form", f"pressing '{label}' on step {step_no} did not advance the form")
            return page
        steps.append(f"step {step_no}: filled {sorted(k for k, ok in filled.items() if ok)}, pressed '{label}'")
    else:
        _manual(result, "multi_step_form", f"form has more than {MAX_FORM_STEPS} steps")
        return page

    # ── Final gates: never send an empty or partial application ──
    if not uploads["cv"]:
        _manual(result, "required_fields_unfilled", "no CV upload field was found on any step")
        return page
    if not any(filled.values()):
        _manual(result, "required_fields_unfilled", "could not fill any contact field")
        return page
    blocker = human_blocker(page, scan_form(page), before_send=True)
    if blocker and not _handoff(page, ctx, result, blocker, before_send=True):
        return page

    summary = (f"{sum(filled.values())} fields, CV{' + cover letter' if uploads['letter'] else ''} uploaded")
    _inspect_log(ctx, f"FINAL FORM STATE: {summary}")
    _inspect_pause(ctx, "FINAL before submit/approval")
    if not ctx["auto_approve"]:
        result["status"] = "awaiting_approval"
        result["note"] = f"Form filled completely ({summary}); not submitted (auto-submit off)"
        if not ctx["headless"]:
            ctx["emit"]("Form is ready. Check it in the browser window and press submit yourself; close the window when done.")
            try:
                page.wait_for_event("close", timeout=HEADED_MANUAL_WAIT_MS)
            except Exception:  # noqa: BLE001
                pass
        return page

    _log("Submitting application...")
    _inspect_pause(ctx, "AUTO-SUBMIT: final click")
    submit_btn.click(timeout=5000)
    confirmed, why = verify_submission(page)
    if confirmed:
        result["status"], result["submitted"] = "submitted", True
        result["note"] = f"Application submitted ({summary}; {why})"
    elif why.startswith("validation"):
        _manual(result, "validation_errors", why)
    else:
        _manual(result, "submission_unconfirmed", f"{why}. Submit WAS clicked - check your inbox before applying again")
    return page


def apply_to_job(
    url: str,
    headless: bool = True,
    auto_approve: bool = False,
    max_wait_seconds: int = 30,
    cv_path: Optional[Path] = None,
    letter_path: Optional[Path] = None,
    assist: bool = False,
    inspect: bool = False,
    accept_privacy: Optional[bool] = None,
    human_wait_seconds: Optional[int] = None,
    on_event: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Run one application attempt and return a structured result dict:
      status, blocker, note, steps, filled_fields, uploaded_docs,
      required_unfilled, submitted, final_url, screenshot, apply_email

    assist=True  -> visible browser with a persistent profile; captcha / login /
                    bot checks are handed to the person and the run continues
                    once they are cleared.
    inspect=True -> visible browser, verbose logging, pauses at each major step
                    (form detection, fill, upload, next/submit) for live inspection.
                    Implies assist=True and headless=False.
    """
    result: Dict[str, Any] = {
        "url": url, "final_url": url, "status": "error", "blocker": None, "note": "", "steps": [],
        "filled_fields": {}, "uploaded_docs": {"cv": False, "letter": False}, "required_unfilled": [],
        "submitted": False, "screenshot": None, "apply_email": None,
    }

    # Resolve THIS job's documents. Never fall back to another job's folder:
    # sending company A's cover letter to company B is worse than not applying.
    if cv_path is None or letter_path is None:
        folder = load_history().get(url, {}).get("folder_name")
        if folder:
            cv_path = cv_path or APPLICATIONS_DIR / folder / "CV.pdf"
            letter_path = letter_path or APPLICATIONS_DIR / folder / "Anschreiben.pdf"
    if not (cv_path and letter_path and Path(cv_path).is_file() and Path(letter_path).is_file()):
        result["note"] = "CV.pdf / Anschreiben.pdf for this job not found - generate documents first"
        return _finish(result)
    cv_path, letter_path = Path(cv_path), Path(letter_path)
    if inspect:
        assist = True
        headless = False   # inspection needs a visible window
    if assist:
        headless = False   # a person can only help in a window they can see

    def emit(msg: str) -> None:
        _log(msg)
        if on_event:
            try:
                on_event(msg)
            except Exception:  # noqa: BLE001
                pass

    ctx: Dict[str, Any] = {
        "assist": assist, "headless": headless, "auto_approve": auto_approve, "emit": emit,
        "inspect": inspect,
        "accept_privacy": ACCEPT_PRIVACY if accept_privacy is None else accept_privacy,
        "human_wait_s": HUMAN_WAIT_S if human_wait_seconds is None else human_wait_seconds,
        "answers": job_specific_answers(url, cv_path.parent, load_candidate()),
        "cv_path": cv_path, "letter_path": letter_path,
    }

    with sync_playwright() as p:
        try:
            closable, context = _open_browser(p, headless, persistent=assist)
        except Exception as exc:  # noqa: BLE001 - no browser binary, bad channel/path, profile in use
            result["blocker"] = "browser_unavailable"
            result["note"] = ("Browser could not be started - run 'playwright install chromium' in the project venv "
                              f"or set JOB_APPLIER_BROWSER_CHANNEL=chrome ({str(exc).splitlines()[0][:120]})")
            return _finish(result)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            _log(f"Navigating to: {url}")
            response = page.goto(url, wait_until="domcontentloaded", timeout=max_wait_seconds * 1000)
            page.wait_for_timeout(2000)
            page = _run(page, ctx, result, response.status if response else None)
            result["final_url"] = page.url
        except Exception as exc:  # noqa: BLE001
            result["status"] = "error"
            result["note"] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
        finally:
            try:
                if not page.is_closed():
                    shot = cv_path.parent / "apply_result.png"
                    page.screenshot(path=str(shot), full_page=True, timeout=8000)
                    result["screenshot"] = str(shot)
            except Exception:  # noqa: BLE001
                pass
            try:
                closable.close()
            except Exception:  # noqa: BLE001
                pass

    return _finish(result)


def _finish(result: Dict[str, Any]) -> Dict[str, Any]:
    """Emit one structured, machine-readable log line per attempt."""
    summary = {k: result[k] for k in (
        "status", "blocker", "note", "final_url", "filled_fields", "uploaded_docs", "required_unfilled", "steps",
    )}
    _log("RESULT " + json.dumps(summary, ensure_ascii=False))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Job Applier Browser Agent")
    parser.add_argument("url", help="Job application URL")
    parser.add_argument("--headless", action="store_true", default=True, help="Run browser headless")
    parser.add_argument("--headed", action="store_true", help="Run browser with visible window")
    parser.add_argument("--assist", action="store_true", help="Visible browser; pause for you on captcha/login, then continue")
    parser.add_argument("--inspect", action="store_true", help="Visible browser, verbose logging, pause at each step (form scan, fill, upload, next/submit)")
    parser.add_argument("--approve", action="store_true", help="Auto-submit without approval pause")
    parser.add_argument("--accept-privacy", action="store_true", help="Tick required privacy/consent checkboxes")
    parser.add_argument("--max-wait", type=int, default=30, help="Max wait seconds for page load")
    args = parser.parse_args()

    result = apply_to_job(
        url=args.url,
        headless=args.headless and not args.headed,
        auto_approve=args.approve,
        max_wait_seconds=args.max_wait,
        assist=args.assist,
        inspect=args.inspect,
        accept_privacy=True if args.accept_privacy else None,
    )

    status = result["status"] if result["status"] in ("submitted", "awaiting_approval", "needs_manual") else "failed"
    record_submission(
        history=load_history(), url=result["url"], status=status,
        note=result["note"], blocker=result["blocker"],
    )
    return 0 if result["status"] in ("submitted", "awaiting_approval") else 1


if __name__ == "__main__":
    sys.exit(main())
