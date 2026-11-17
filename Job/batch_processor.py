"""
Batch Processor: Orchestrator for Job Application Generation
=============================================================
Reads targets.json, enforces strict Werkstudent/Vollzeit + Germany scope,
scores each job (geographic tier + tech-stack alignment), sorts the
discovery/review queue with Tier 1 (Dortmund/NRW) first, then generates
tailored CV + cover-letter docs per approved job and updates history.json.

Usage:
    python batch_processor.py

Logs errors to failed_jobs.log.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from relevance import is_relevant_title

# Smart Job Analysis Engine integration
from job_analyzer import analyze_job_description, load_profile as _load_profile

# Live job discovery is imported lazily in main() so that importing this module
# (e.g. from api_server) does not require playwright.

# ── Paths ───────────────────────────────────────────────────────────────────
WORKSPACE_ROOT = Path(__file__).resolve().parent
TARGETS_PATH = WORKSPACE_ROOT / "targets.json"
HISTORY_PATH = WORKSPACE_ROOT / "history.json"
PREVIOUSLY_DONE_PATH = WORKSPACE_ROOT / "previously_done.txt"
PROFILE_PATH = WORKSPACE_ROOT / "profile.json"
APPLICATIONS_DIR = WORKSPACE_ROOT / "_applications"
FAILED_LOG = WORKSPACE_ROOT / "failed_jobs.log"

# Venv Python (has reportlab for reliable PDF generation)
VENV_PYTHON = WORKSPACE_ROOT.parent / "web-ui" / "venv" / "Scripts" / "python.exe"
if not VENV_PYTHON.exists():
    # Fallback to system python
    VENV_PYTHON = Path("python")

PAUSE_SECONDS = float(os.getenv("JOB_APPLIER_PAUSE_SECONDS", "2"))
MAX_JOBS = int(os.getenv("JOB_APPLIER_MAX_JOBS", "20"))

# ── Static CV / cover-letter builders ───────────────────────────────────────
STATIC_CV_CANDIDATES = {
    "duales": WORKSPACE_ROOT / "cv_applier" / "cv_duales_studium.pdf",
    "werkstudent": WORKSPACE_ROOT / "cv_applier" / "cv_werkstudent.pdf",
    "vollzeit": WORKSPACE_ROOT / "cv_applier" / "cv_vollzeit.pdf",
    "fallback": WORKSPACE_ROOT / "cv_applier" / "output.pdf",
}
LETTER_BUILDER = WORKSPACE_ROOT / "cover_applier" / "build_cover_letter.py"

# ── Role scope: ACCEPT ONLY Werkstudent / Vollzeit ──────────────────────────
IN_SCOPE_ROLE_KEYWORDS = ["werkstudent", "vollzeit", "full-time", "full time"]

# Automatically reject: internships, ausbildung, minijob, freelance, senior/lead (5+ yrs)
OUT_OF_SCOPE_ROLE_KEYWORDS = [
    "praktikum", "internship", "ausbildung", "duales studium", "dual studium",
    "minijob", "450-euro", "450 euro", "freelance", "freiberuflich",
    "senior", "lead", "leitend", "team lead", "head of", "principal",
    "5+ jahre", "5 jahre", "5+ years", "five years", "5+ jahre erfahrung",
]

# ── Geographic scope & priority tiering (all of Germany) ────────────────────
TIER_1_CITIES = [
    "dortmund", "bochum", "essen", "düsseldorf", "duisburg", "gelsenkirchen",
    "hagen", "witten", "oberhausen", "münster", "köln", "cologne",
]
TIER_1_POINTS = 50
TIER_2_POINTS = 15

GERMANY_INDICATORS = [
    "deutschland", "germany", "deutsche", "deutsch",
    "berlin", "münchen", "munich", "hamburg", "köln", "cologne",
    "frankfurt", "stuttgart", "düsseldorf", "leipzig", "dortmund",
    "essen", "bremen", "dresden", "hannover", "nürnberg", "nuremberg",
    "bonn", "münster", "karlsruhe", "mannheim", "augsburg", "wiesbaden",
    "remote", "deutschlandweit", "bundesweit", "homeoffice",
]

# Candidate profile tech stack (for overlap scoring)
PROFILE_TECH_KEYWORDS = [
    "python", "c++", "cpp", "ros", "ros 2", "pytorch", "opencv", "computer vision",
    "docker", "kotlin", "git", "automation", "robotics", "machine learning",
    "ml", "ai", "software engineering", "softwareentwicklung", "linux",
    "sql", "postgresql", "mongodb", "aws", "azure", "kubernetes", "k8s",
]


def load_targets() -> List[Dict[str, Any]]:
    if not TARGETS_PATH.exists():
        return []
    with open(TARGETS_PATH, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("targets", "jobs", "items"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    raise ValueError("targets.json must contain a JSON array or object.")


def load_history() -> Dict[str, Any]:
    if HISTORY_PATH.exists():
        with open(HISTORY_PATH, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    return {}


def save_history(history: Dict[str, Any]) -> None:
    """Atomically persist history.json to avoid partial writes on crashes."""
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=HISTORY_PATH.parent, delete=False, suffix=".tmp"
    ) as tmp:
        json.dump(history, tmp, ensure_ascii=False, indent=2)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_path = Path(tmp.name)
    os.replace(temp_path, HISTORY_PATH)


def log_failure(url: str, error: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(FAILED_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {url}: {error}\n")


def load_previously_done() -> set:
    if PREVIOUSLY_DONE_PATH.exists():
        with open(PREVIOUSLY_DONE_PATH, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def save_job_signature(signature: str) -> None:
    with open(PREVIOUSLY_DONE_PATH, "a", encoding="utf-8") as f:
        f.write(f"{signature}\n")


def extract_job_signature(company: str, job_title: str) -> str:
    return f"{company}_{job_title}".lower()


def history_signatures(history: Dict[str, Any]) -> set:
    """company+title signatures of every job currently in history.json."""
    return {
        extract_job_signature(sanitize_name(e.get("company") or "Unknown"),
                              sanitize_name(e.get("job_title") or "Unknown"))
        for e in history.values()
    }


# ── Role detection (strict) ─────────────────────────────────────────────────
def _has_any(text: str, keywords: List[str]) -> bool:
    return any(kw in text for kw in keywords)


_WORD_CHARS = "a-z0-9äöüß"
_UMLAUT_MAPS = (
    {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"},
    {"ä": "a", "ö": "o", "ü": "u", "ß": "ss"},
)


def _variants(kw: str) -> List[str]:
    """Keyword plus its ASCII spellings (köln -> koeln, koln) as used in URLs."""
    out = [kw]
    for table in _UMLAUT_MAPS:
        v = "".join(table.get(ch, ch) for ch in kw)
        if v not in out:
            out.append(v)
    return out


def _has_word(text: str, kw: str) -> bool:
    """Whole-word match, so 'ai' does not hit 'gmail' and 'essen' not 'interessen'."""
    for v in _variants(kw.lower()):
        if re.search(rf"(?<![{_WORD_CHARS}]){re.escape(v)}(?![{_WORD_CHARS}])", text):
            return True
    return False


def _has_any_word(text: str, keywords: List[str]) -> bool:
    return any(_has_word(text, kw) for kw in keywords)


# Short / ambiguous reject keywords that must match as whole words only
_WHOLE_WORD_REJECTS = {"lead", "senior", "principal"}


def detect_job_type(job_title_raw: str, job_description: str = "") -> Optional[str]:
    """
    Strict role detection.

    Returns "werkstudent" or "vollzeit" only for in-scope roles.
    Returns None for out-of-scope roles (internship/praktikum, ausbildung,
    minijob, freelance, senior/lead requiring 5+ years, duales Studium).

    Any role that is NOT explicitly an internship/praktikum/ausbildung/minijob/
    freelance is assumed to be Vollzeit (most German job postings don't
    explicitly say "Vollzeit" in the title).
    """
    text = f"{job_title_raw} {job_description}".lower()

    # Hard rejects first
    for kw in OUT_OF_SCOPE_ROLE_KEYWORDS:
        if (_has_word(text, kw) if kw in _WHOLE_WORD_REJECTS else kw in text):
            return None

    if "werkstudent" in text:
        return "werkstudent"

    # Default: assume Vollzeit (regular job posting)
    return "vollzeit"


def get_salary_string(job_type: Optional[str]) -> str:
    if job_type == "werkstudent":
        return "Gehaltswunsch: 18.000 EUR (Bruttojahresgehalt)"
    return "Gehaltswunsch: 52.000 EUR (Bruttojahresgehalt)"


# ── Geographic tiering ──────────────────────────────────────────────────────
def get_geographic_tier(location_raw: str, job_description: str = "") -> Tuple[int, str]:
    """
    Returns (tier, tier_label).
      Tier 1 (+50): Dortmund + Ruhr/NRW neighbors.
      Tier 2 (+15): All other Germany / Remote-in-Germany.
      Tier 0 (reject): Outside Germany or non-EU relocation without local eligibility.
    """
    haystack = f"{location_raw} {job_description}".lower()

    # Tier 1: Dortmund + Ruhr/NRW neighbors
    if _has_any_word(haystack, TIER_1_CITIES):
        return 1, "Tier 1 (Dortmund/NRW)"

    # Germany-wide indicators -> Tier 2
    if _has_any_word(haystack, GERMANY_INDICATORS) or "deutsch" in haystack:
        return 2, "Tier 2 (All Germany)"

    # Tier 0: outside Germany / non-EU relocation without local eligibility
    return 0, "Tier 0 (Outside Germany / Reject)"


# ── Tech stack & language extraction ────────────────────────────────────────
TECH_ALIASES = {"cpp": "c++", "ros 2": "ros", "ros2": "ros", "ml": "machine learning", "k8s": "kubernetes"}


def extract_tech_stack(text: str) -> List[str]:
    """Profile technologies mentioned in the text (whole-word, aliases folded)."""
    text_lower = text.lower()
    result: List[str] = []
    for kw in PROFILE_TECH_KEYWORDS + ["ros2"]:
        if _has_word(text_lower, kw):
            canonical = TECH_ALIASES.get(kw, kw)
            if canonical not in result:
                result.append(canonical)
    return result


GERMAN_CITIES = [
    "berlin", "münchen", "munich", "hamburg", "köln", "cologne",
    "frankfurt", "stuttgart", "düsseldorf", "leipzig", "dortmund",
    "essen", "bremen", "dresden", "hannover", "nürnberg", "nuremberg",
    "bonn", "münster", "karlsruhe", "mannheim", "augsburg", "wiesbaden",
    "aachen", "bielefeld", "bochum", "bonn", "braunschweig", "chemnitz",
    "krefeld", "ludwigshafen", "magdeburg", "mainz", "potsdam",
    "regensburg", "rostock", "saarbrücken", "schwerin", "trier",
    "ulm", "wiesbaden", "wuppertal", "würzburg",
]

ENGLISH_MARKERS = [
    "english", "englisch", "fluent english", "good english",
    "native speaker", "business english", "english required",
]

def detect_language(text: str, location: str = "") -> str:
    text_lower = text.lower()
    loc_lower = location.lower()
    
    # If location is a German city, default to German
    if any(city in loc_lower for city in GERMAN_CITIES):
        # Only mark as English if explicit English requirement found
        if any(m in text_lower for m in ENGLISH_MARKERS):
            return "English"
        return "German"
    
    # German keywords in text
    german_markers = [
        "deutsch", "deutsche", "deutschkenntnisse", "fließend deutsch",
        "gute deutschkenntnisse", "verhandlungssicher", "muttersprachlich",
    ]
    if any(m in text_lower for m in german_markers):
        return "German"
    
    return "English"


def compute_match_score(
    job_type: Optional[str],
    tier: int,
    tech_stack: List[str],
    profile: Dict[str, Any],
) -> int:
    """Overall Match Score (0-100): geographic tier + tech-stack alignment."""
    score = 0

    # Geographic tier
    if tier == 1:
        score += TIER_1_POINTS
    elif tier == 2:
        score += TIER_2_POINTS
    else:
        return 0  # Tier 0 -> reject

    # Tech stack overlap with candidate profile
    # tech_stack only contains PROFILE_TECH_KEYWORDS hits. If profile.json has an
    # explicit "skills" list, restrict the overlap to it.
    skills = [str(s).lower() for s in profile.get("skills", [])] if isinstance(profile, dict) else []
    overlap = [kw for kw in tech_stack if not skills or kw.lower() in skills]
    score += min(30, len(overlap) * 6)

    # Role type fit (Werkstudent/Vollzeit already enforced)
    score += 10

    return min(100, score)


# ── Full job analysis & scoring ─────────────────────────────────────────────
def analyze_and_score_job(job: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    """Produce a complete analysis dict: role type, tier, tech stack, language, score."""
    job_title_raw = (
        job.get("job_title") or job.get("position_target") or job.get("title") or ""
    )
    job_description = job.get("job_description", "") or ""
    location_raw = (
        job.get("location", "")
        or job.get("city", "")
        or job.get("ort", "")
        or job.get("region", "")
        or job.get("country", "")
        or ""
    )
    company_raw = job.get("company", "") or ""
    url = job.get("url", "") or ""

    # ── Fallback: extract info from URL when fields missing ──
    url_lower = url.lower()

    # Extract company name from StepStone URLs:
    # Pattern: stellenangebote--JobTitle--City--CompanyName--ID-inline.html
    if not company_raw and "stepstone.de" in url_lower:
        m = re.search(r"--([A-Za-zÄÖÜäöüß\s&.-]+)--\d+--inline", url_lower)
        if m:
            company_raw = m.group(1).strip()
        else:
            m2 = re.search(r"--([A-Za-zÄÖÜäöüß\s&.-]+)--\d+-", url_lower)
            if m2:
                company_raw = m2.group(1).strip()

    # Clean company name: replace underscores, hyphens with spaces, fix casing
    # Preserve German legal-form abbreviations (GmbH, KG, SE, AG, Co., etc.)
    GERMAN_ABBREV = {"gmbh": "GmbH", "kg": "KG", "se": "SE", "ag": "AG", "co.": "Co.", "gmbh&co.kg": "GmbH & Co. KG"}
    if company_raw:
        company_raw = company_raw.replace("_", " ").replace("-", " ")
        words = company_raw.split()
        fixed = []
        for w in words:
            wl = w.lower().rstrip(".")
            if wl in GERMAN_ABBREV:
                fixed.append(GERMAN_ABBREV[wl])
            else:
                fixed.append(w.capitalize())
        company_raw = " ".join(fixed)

    if not job_title_raw:
        if "werkstudent" in url_lower:
            job_title_raw = "Werkstudent"
        elif "vollzeit" in url_lower:
            job_title_raw = "Vollzeit"
        elif "junior" in url_lower:
            job_title_raw = "Junior"
    if not location_raw:
        for city in TIER_1_CITIES + ["berlin", "münchen", "munich", "hamburg", "frankfurt",
                                       "stuttgart", "köln", "cologne", "leipzig", "essen",
                                       "düsseldorf", "duisburg", "gelsenkirchen", "hagen",
                                       "witten", "oberhausen", "münster", "bremen", "dresden",
                                       "hannover", "nürnberg", "nuremberg", "bonn", "karlsruhe",
                                       "mannheim", "augsburg", "wiesbaden"]:
            if _has_word(url_lower, city):
                location_raw = city.title()
                break

    job_type = detect_job_type(job_title_raw, job_description)
    tier, tier_label = get_geographic_tier(location_raw, job_description + " " + url_lower)
    tech_stack = extract_tech_stack(f"{job_title_raw} {job_description} {url}")
    # Default to German for German job postings (all targets are German)
    language = detect_language(f"{job_title_raw} {job_description} {url}", location_raw)
    if language == "English" and location_raw and location_raw != "Unbekannt":
        language = "German"
    match_score = compute_match_score(job_type, tier, tech_stack, profile)
    relevant, relevance_reason = is_relevant_title(job_title_raw)

    return {
        "job_title": job_title_raw,
        "job_type": job_type or "Unbekannt",
        "tier": tier,
        "tier_label": tier_label,
        "location": location_raw or "Unbekannt",
        "language": language,
        "tech_stack": tech_stack,
        "match_score": match_score,
        "relevant": relevant,
        "relevance_reason": relevance_reason,
        # Domain gate first: non-technical roles never reach document
        # generation or the LLM analysis.
        "in_scope": relevant and job_type is not None and tier != 0,
        "company": company_raw or "Unknown",
    }


# ── File / payload helpers ──────────────────────────────────────────────────
def sanitize_name(name: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9]", "_", name)
    return name[:20]


def _url_tag(url: str) -> str:
    """Short stable suffix so two jobs with the same truncated names never share a folder."""
    import hashlib
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:6]


def get_static_cv_path(job_type: Optional[str]) -> Path:
    candidate = STATIC_CV_CANDIDATES.get(job_type, STATIC_CV_CANDIDATES["fallback"])
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"No static CV PDF found for job type '{job_type}'.")


def build_job_payloads(company: str, job_title_raw: str, job_dir: Path, job_type: Optional[str]) -> Path:
    # Determine language-appropriate salutation
    salutation = "Sehr geehrte Damen und Herren,"

    # Personalized paragraph 1 based on job type
    if job_type == "werkstudent":
        p1 = (f"Mit großem Interesse bewerbe ich mich um die ausgeschriebene Stelle als Werkstudent bei {company}. "
              f"Ich habe meinen B.Sc. in Informatik an der TU Dortmund abgeschlossen und bringe ein fundiertes Profil "
              f"in Computer Vision, Machine Learning und Softwareentwicklung mit.")
    else:
        p1 = (f"Mit großem Interesse bewerbe ich mich um die ausgeschriebene Vollzeitstelle bei {company}. "
              f"Ich habe meinen B.Sc. in Informatik an der TU Dortmund abgeschlossen und bringe ein fundiertes Profil "
              f"in Computer Vision, Machine Learning und Softwareentwicklung mit.")

    # Generic but professional paragraph 2
    p2 = ("Während meines Studiums und meiner bisherigen Projekterfahrung habe ich mich intensiv mit "
          "Systemarchitektur, Softwareentwicklung und dem Umgang mit modernen Technologiestacks beschäftigt. "
          "Ich arbeite gerne im Team und bin es gewohnt, komplexe technische Aufgaben selbstständig zu lösen.")

    p3 = "Ich bin ab sofort verfügbar und freue mich auf ein persönliches Gespräch."

    letter_payload = {
        "company_name": company,
        "company_address": "",  # No fake address - leave blank if unknown
        "job_title": job_title_raw,
        "salutation": salutation,
        "paragraph_1": p1,
        "paragraph_2": p2,
        "paragraph_3": p3,
    }
    # Profile constraint: letters present the completed B.Sc. only.
    for text in (p1, p2, p3):
        if re.search(r"master", text, re.I):
            raise ValueError("Cover letter text must not mention Master's studies")

    letter_payload_path = job_dir / "cover_letter_payload.json"
    letter_payload_path.write_text(
        json.dumps(letter_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return letter_payload_path


def format_subprocess_error(script_name: str, result) -> str:
    stderr = result.stderr.strip()
    stdout = result.stdout.strip()
    message = stderr or stdout or f"{script_name} failed with code {result.returncode}"
    normalized = message.lower()
    if "prompt tokens limit exceeded" in normalized or "prompt token" in normalized:
        return (
            f"{script_name} failed due to OpenRouter prompt token limits. "
            f"This may be fixed by reducing the job posting text or your max_tokens setting. "
            f"Original error: {message}"
        )
    if "credits" in normalized and "402" in normalized:
        return (
            f"{script_name} failed due to OpenRouter credit limits. "
            f"Check your OpenRouter account or lower max_tokens. "
            f"Original error: {message}"
        )
    return f"{script_name} failed: {message}"


def is_in_scope_job(job: Dict[str, Any], profile: Dict[str, Any]) -> bool:
    """Return True only for Werkstudent/Vollzeit roles in Germany (Tier 1 or Tier 2)."""
    analysis = analyze_and_score_job(job, profile)
    return analysis["in_scope"]


def process_job(job, history, previously_done, profile):
    url = job.get("url", "")

    # ── SCOPE FILTER: Werkstudent / Vollzeit, Germany (Tier 1 or Tier 2) ──
    analysis = analyze_and_score_job(job, profile)
    if not analysis["in_scope"]:
        why = (f"not a technical role ({analysis['relevance_reason']})" if not analysis["relevant"]
               else f"{analysis['job_type']} / {analysis['tier_label']}")
        print(f"[SKIP] Out of scope: {why} :: {analysis['job_title'][:60]} :: {url}")
        return

    company_raw_display = analysis.get("company", job.get("company", "Unknown"))
    company = sanitize_name(company_raw_display)
    job_title_raw = analysis["job_title"] or "Unknown"
    job_title = sanitize_name(job_title_raw)

    # ── DUPLICATE GUARD ────────────────────────────────────────────────────
    # history.json is the single source of truth: a job is a duplicate only if
    # a DIFFERENT url with the same company+title is currently in history.
    # Removing an entry from history.json therefore always allows regeneration.
    job_signature = extract_job_signature(company, job_title)
    if job_signature in previously_done:
        print(f"[SKIP] Same company + title already in history under another URL: {job_signature}")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    folder_name = f"{today}_{company}_{job_title}_{_url_tag(url)}"
    job_dir = APPLICATIONS_DIR / folder_name
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        static_cv_path = get_static_cv_path(analysis["job_type"] if analysis["job_type"] != "Unbekannt" else None)
        shutil.copy2(static_cv_path, job_dir / "CV.pdf")

        letter_payload_path = build_job_payloads(company_raw_display, job_title_raw, job_dir, analysis["job_type"])

        print(f"    -> Copying static CV PDF for {company}...")
        print(f"       Source: {static_cv_path}")
        print(f"       Routed job type: {analysis['job_type']}")

        print(f"    -> Running cover_applier/build_cover_letter.py for {company}...")
        result = subprocess.run(
            [
                str(VENV_PYTHON),
                str(LETTER_BUILDER),
                str(letter_payload_path),
                "--output-tex", str(job_dir / "Anschreiben.tex"),  # pdflatex names the PDF after the .tex
                "--output-pdf", str(job_dir / "Anschreiben.pdf"),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(format_subprocess_error("cover_applier/build_cover_letter.py", result))

        # ── CREATE APPLY_DATA.TXT WITH SALARY INJECTION ──────────────────────
        apply_data = f"{url}\n"
        apply_data += f"{get_salary_string(analysis['job_type'])}\n"
        with open(job_dir / "apply_data.txt", "w", encoding="utf-8") as f:
            f.write(apply_data)

        # ── Update history with default pending status + analysis ────────────
        # Check which documents were actually generated
        from doc_quality import letter_problem
        cv_exists = (job_dir / "CV.pdf").exists()
        letter_exists = (job_dir / "Anschreiben.pdf").exists()
        problem = letter_problem(job_dir / "Anschreiben.pdf")
        if problem:
            raise RuntimeError(problem)
        docs_ready = cv_exists and letter_exists

        history[url] = {
            "processed_at": today,
            "status": "success" if docs_ready else "docs_failed",
            "application_status": "pending" if docs_ready else "failed",
            "application_status_label": "Pending / Waiting" if docs_ready else "Docs Failed / Dropped",
            "status_updated_at": today,
            "status_source": "pipeline",
            "company": analysis.get("company", job.get("company", "Unknown")),
            "job_title": job_title_raw,
            "location": analysis["location"],
            "tier": analysis["tier"],
            "tier_label": analysis["tier_label"],
            "job_type": analysis["job_type"],
            "language": analysis["language"],
            "tech_stack": analysis["tech_stack"],
            "match_score": analysis["match_score"],
            "docs_ready": docs_ready,
            "cv_exists": cv_exists,
            "letter_exists": letter_exists,
            "job_dir": str(job_dir),
            "folder_name": folder_name,
        }

        # ── Smart Job Analysis (LLM + heuristics) ────────────────────────────
        try:
            job_desc = job.get("job_description", "") or ""
            if job_desc.strip():
                llm_analysis = analyze_job_description(url, job_desc, profile)
                history[url]["llm_analysis"] = {
                    "match_score": llm_analysis.get("match_score", analysis["match_score"]),
                    "tech_stack": llm_analysis.get("tech_stack", analysis["tech_stack"]),
                    "key_requirements": llm_analysis.get("key_requirements", []),
                    "summary": llm_analysis.get("summary", ""),
                    "analyzed_at": llm_analysis.get("analyzed_at", ""),
                }
                print(
                    f"    -> Analysis: match_score={llm_analysis.get('match_score', 0)}, "
                    f"job_type={llm_analysis.get('job_type', '')}, location={llm_analysis.get('location', '')}"
                )
        except Exception as exc:
            print(f"    -> LLM analysis skipped: {exc}")

        print(f"Processed: {folder_name} (tier={analysis['tier']}, score={analysis['match_score']})")

        previously_done.add(job_signature)

    except Exception as e:
        log_failure(url, str(e))
        print(f"Failed: {url} - {e}")


def score_and_sort_targets(targets: List[Dict[str, Any]], profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Attach analysis to each target and sort: Tier 1 first, then highest match score."""
    scored = []
    for job in targets:
        analysis = analyze_and_score_job(job, profile)
        job = dict(job)
        job["_analysis"] = analysis
        scored.append(job)
    scored.sort(
        key=lambda j: (
            j["_analysis"]["tier"] if j["_analysis"]["tier"] in (1, 2) else 9,  # Tier 1, Tier 2, then Tier 0
            -j["_analysis"]["match_score"],   # highest score first
        )
    )
    return scored


def filter_already_applied(targets: List[Dict[str, Any]], history: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Remove targets that already exist in history (already applied)."""
    filtered = []
    for job in targets:
        url = job.get("url", "")
        if url not in history:
            filtered.append(job)
    return filtered


def execute_approved_applications(headless: bool = True, auto_approve: bool = False) -> Dict[str, Any]:
    """
    Execute approved applications using the browser agent.

    Reads history.json for jobs with status 'approved' (set in the dashboard's
    Review tab), launches the browser agent for each with that job's own
    CV/cover letter, and updates history with results.
    """
    from apply_browser_agent import apply_to_job, record_submission

    history = load_history()

    pending = []
    for url, entry in history.items():
        if entry.get("docs_ready") and entry.get("application_status") == "approved":
            pending.append((url, entry))

    if not pending:
        print("No approved applications with ready documents to execute. Approve jobs in the dashboard first.")
        return {"executed": 0, "failed": 0, "awaiting": 0}

    print(f"Found {len(pending)} approved application(s) to execute.\n")

    executed = 0
    failed = 0
    awaiting = 0

    for url, entry in pending:
        company = entry.get("company", "Unknown")
        job_title = entry.get("job_title", "")
        print(f"\n{'='*60}")
        print(f"Executing: {company} — {job_title}")
        print(f"URL: {url}")
        print(f"{'='*60}")

        job_dir = APPLICATIONS_DIR / (entry.get("folder_name") or "__missing__")
        result = apply_to_job(
            url=url,
            headless=headless,
            auto_approve=auto_approve,
            cv_path=job_dir / "CV.pdf",
            letter_path=job_dir / "Anschreiben.pdf",
        )

        if result["status"] == "needs_manual":
            record_submission(history, url, "needs_manual", result["note"], company, job_title, result.get("blocker"))
            failed += 1
            print(f"  -> NEEDS MANUAL [{result.get('blocker')}]: {result['note']}")
        elif result["status"] == "submitted":
            record_submission(history, url, "submitted", result["note"], company, job_title)
            executed += 1
            print(f"  -> SUBMITTED: {result['note']}")
        elif result["status"] == "awaiting_approval":
            record_submission(history, url, "awaiting_approval", result["note"], company, job_title)
            awaiting += 1
            print(f"  -> AWAITING APPROVAL: {result['note']}")
        else:
            record_submission(history, url, "failed", result["note"], company, job_title)
            failed += 1
            print(f"  -> FAILED: {result['note']}")

    print(f"\n{'='*60}")
    print(f"EXECUTION SUMMARY: {executed} submitted, {awaiting} awaiting approval, {failed} failed")
    print(f"{'='*60}")

    return {"executed": executed, "failed": failed, "awaiting": awaiting}


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Job Applier Batch Processor")
    parser.add_argument("--execute", action="store_true", help="Execute approved applications via browser agent")
    parser.add_argument("--headless", action="store_true", default=True, help="Run browser headless")
    parser.add_argument("--headed", action="store_true", help="Run browser with visible window")
    parser.add_argument("--approve", action="store_true", help="Auto-submit without approval pause")
    parser.add_argument("--no-discover", action="store_true", help="Skip live job discovery; only generate docs for existing targets")
    args = parser.parse_args()

    headless = args.headless and not args.headed

    if args.execute:
        print("=== EXECUTE MODE: Submitting approved applications ===\n")
        result = execute_approved_applications(headless=headless, auto_approve=args.approve)
        return

    profile = _load_profile()

    # ── Auto-discover new jobs (skipped with --no-discover) ──
    if not args.no_discover:
        from job_scraper import update_targets_file as _update_targets_file

        print("\n" + "=" * 60)
        print("JOB DISCOVERY: querying live job sources...")
        print("=" * 60)
        discovery_result = _update_targets_file(headless=headless, max_jobs=MAX_JOBS)
        print(f"Discovery: {discovery_result['new']} new jobs found "
              f"({discovery_result['existing']} existing + {discovery_result['new']} new = {discovery_result['total']} total)\n")

    targets = load_targets()
    history = load_history()
    # Signatures come from history.json (not previously_done.txt), so clearing
    # a history entry is enough to make its job eligible again.
    previously_done = history_signatures(history)

    if not targets:
        print("No targets found in targets.json. Please inject URLs via the browser JS payload.")
        return

    # ── Filter out already-applied jobs ──
    targets = filter_already_applied(targets, history)
    if not targets:
        print("All targets already applied (present in history.json). Nothing new to process.")
        return

    # ── Sort discovery/review queue: Tier 1 (Dortmund/NRW) highest first ──
    scored = score_and_sort_targets(targets, profile)

    in_scope_count = sum(1 for j in scored if j["_analysis"]["in_scope"])
    off_domain = sum(1 for j in scored if not j["_analysis"]["relevant"])
    print(f"Loaded {len(scored)} new targets; {in_scope_count} in scope "
          f"(technical role, Werkstudent/Vollzeit, Germany); {off_domain} dropped as non-technical.")

    processed = 0
    for job in scored:
        if processed >= MAX_JOBS:
            break
        analysis = job["_analysis"]
        if not analysis["in_scope"]:
            continue
        url = job.get("url", "")
        had_entry = url in history
        process_job(job, history, previously_done, profile)
        processed += 1
        if url in history and not had_entry:
            # Merge just this entry into the freshest history on disk, so status
            # changes made in the dashboard while we were running are kept and a
            # crash later in the batch does not lose finished jobs.
            fresh = load_history()
            fresh[url] = history[url]
            save_history(fresh)
            if PAUSE_SECONDS > 0:
                time.sleep(PAUSE_SECONDS)

    print("Batch processing complete.")


if __name__ == "__main__":
    main()
