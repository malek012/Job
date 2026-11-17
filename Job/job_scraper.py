#!/usr/bin/env python3
"""
Job Applier - Multi-Source Job Aggregator.

Aggregates fresh job postings from multiple German sources:
  1. Bundesagentur für Arbeit API (public, no-auth)
  2. Arbeitnow API (public, no-key)
  3. Stellenwerk Dortmund (Playwright browser automation)
  4. StepStone (Playwright browser automation fallback)

Normalizes all sources into the targets.json schema, deduplicates
against both targets.json AND history.json, and returns fresh jobs.

Usage:
    python job_scraper.py [--headless] [--max-jobs 50]

Output:
    Appends new job entries to Job/targets.json (deduplicated).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure Job/ is on sys.path
_JOB_DIR = Path(__file__).resolve().parent
if str(_JOB_DIR) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(_JOB_DIR))

try:
    import requests
except ImportError:
    print("FATAL: requests not installed. Install with: pip install requests")
    sys.exit(1)

# playwright is imported lazily in scrape_all_jobs(): the API sources work without it.

from relevance import is_relevant_title  # noqa: E402

TARGETS_PATH = _JOB_DIR / "targets.json"
HISTORY_PATH = _JOB_DIR / "history.json"

# ── Bundesagentur für Arbeit API (public, no-auth) ──────────────────────────

# v4 was retired (requests fail); the Jobsuche site itself now calls v6, which
# uses a different response schema. parse_ba_response() understands both.
BA_API_URLS = [
    "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs",
    "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs",
]
BA_DETAIL_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}"
BA_HEADERS = {
    "X-API-Key": "jobboerse-jobsuche",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
}
# Offer types worth applying to. Werkstudent jobs are filed under PRAKTIKUM_TRAINEE;
# SELBSTAENDIGKEIT entries are mostly paid training courses, AUSBILDUNG is vocational.
BA_ALLOWED_TYPES = {"ARBEIT", "PRAKTIKUM_TRAINEE", ""}

BA_QUERIES = [
    # (was, wo, umkreis km) - Dortmund + 50 km covers the Ruhr area / most of NRW Tier 1
    ("Werkstudent Informatik", "Dortmund", 50),
    ("Werkstudent Softwareentwicklung", "Dortmund", 50),
    ("Softwareentwickler", "Dortmund", 50),
    ("Python Entwickler", "Dortmund", 50),
    ("Machine Learning", "Dortmund", 50),
    ("Computer Vision", "Dortmund", 50),
    ("Embedded Software", "Dortmund", 50),
    ("Robotik", "Dortmund", 50),
]

# ── Arbeitnow Public Job API ──────────────────────────────────────────────────

ARBEITNOW_API_URL = "https://www.arbeitnow.com/api/job-board-api"

ARBEITNOW_KEYWORDS = [
    "werkstudent", "informatik", "computer vision", "machine learning",
    "software", "python", "c++", "developer", "engineer", "robotik",
    "softwareentwicklung", "datenengineering", "ki", "ai",
]
ARBEITNOW_LOCATIONS = [
    "dortmund", "nrw", "bochum", "essen", "düsseldorf", "köln",
    "remote", "germany", "deutschland",
]

# ── Stellenwerk Dortmund (Playwright) ─────────────────────────────────────────

STELLENWERK_URL = "https://www.stellenwerk.de/dortmund/"

# StepStone fallback (Playwright)
STEPSTONE_QUERIES = [
    ("Werkstudent Softwareentwicklung", "Dortmund"),
    ("Werkstudent Informatik", "Dortmund"),
    ("Werkstudent KI Maschinelles Lernen", "Dortmund"),
]

MAX_PAGES_PER_QUERY = 2


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_json_file(path: Path, default: Any = None) -> Any:
    """Load JSON from path, returning default if missing/invalid."""
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_targets(targets: List[Dict[str, Any]]) -> None:
    """Atomically write targets back to targets.json."""
    import os
    tmp_path = TARGETS_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(targets, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, TARGETS_PATH)


def _has_word(text: str, kw: str) -> bool:
    return re.search(rf"(?<![a-z0-9äöüß]){re.escape(kw)}(?![a-z0-9äöüß])", text) is not None


def normalize_job(url: str, company: str, job_title: str, location: str, source: str) -> Dict[str, Any]:
    """Normalize a job into the targets.json schema."""
    return {
        "url": url.strip() if url else "",
        "company": company.strip() if company else "Unknown",
        "job_title": job_title.strip() if job_title else "Unknown",
        "location": location.strip() if location else "Unbekannt",
        "source": source,
    }


def _technical_only(jobs: List[Dict[str, Any]], source: str) -> List[Dict[str, Any]]:
    """Domain gate at discovery: keep only technical roles (see relevance.py)."""
    kept = [j for j in jobs if is_relevant_title(j.get("job_title", ""))[0]]
    if len(kept) != len(jobs):
        print(f"[scraper] {source}: dropped {len(jobs) - len(kept)} non-technical role(s), kept {len(kept)}")
    return kept


def is_duplicate(url: str, existing_urls: set, new_urls: set) -> bool:
    """Check if URL already exists in targets or new batch."""
    return url in existing_urls or url in new_urls


# ── Source 1: Bundesagentur für Arbeit API ───────────────────────────────────

def parse_ba_response(data: Any) -> List[Dict[str, Any]]:
    """
    Normalize a Bundesagentur search response into target entries.

    v6 schema: {"ergebnisliste": [{stellenangebotsTitel, firma, referenznummer,
                 externeURL, stellenangebotsart, stellenlokationen:[{adresse:{ort}}]}]}
    v4 schema: {"stellenangebote": [{titel, arbeitgeber, refnr, externeUrl,
                 arbeitsort:{ort}}]}
    """
    if not isinstance(data, dict):
        return []
    rows = data.get("ergebnisliste")
    if not isinstance(rows, list):
        rows = data.get("stellenangebote")
    if not isinstance(rows, list):
        return []

    jobs: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        offer_type = str(row.get("stellenangebotsart") or "")
        if offer_type not in BA_ALLOWED_TYPES:
            continue
        refnr = row.get("referenznummer") or row.get("refnr") or row.get("hashId") or ""
        extern = row.get("externeURL") or row.get("externeUrl") or row.get("externenUrl") or ""
        url = extern or (BA_DETAIL_URL.format(refnr=refnr) if refnr else "")
        if not url:
            continue
        title = row.get("stellenangebotsTitel") or row.get("titel") or "Unknown"
        company = row.get("firma") or row.get("arbeitgeber") or "Unknown"
        location = ""
        locs = row.get("stellenlokationen")
        if isinstance(locs, list) and locs and isinstance(locs[0], dict):
            location = (locs[0].get("adresse") or {}).get("ort", "") or ""
        if not location and isinstance(row.get("arbeitsort"), dict):
            location = row["arbeitsort"].get("ort", "") or ""
        job = normalize_job(url, str(company), str(title), location, "BA-API")
        if refnr:
            job["ba_refnr"] = refnr
        jobs.append(job)
    return jobs


def _ba_request(params: Dict[str, Any]) -> Optional[Any]:
    """Try each known API version; return parsed JSON or None (with a logged reason)."""
    for api_url in BA_API_URLS:
        version = api_url.rsplit("/", 2)[-2]
        try:
            resp = requests.get(api_url, headers=BA_HEADERS, params=params, timeout=30)
        except Exception as exc:  # noqa: BLE001
            print(f"    [BA] {version}: request failed: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        if resp.status_code != 200:
            print(f"    [BA] {version}: HTTP {resp.status_code} {resp.text[:120]!r}")
            continue
        try:
            return resp.json()
        except ValueError:
            print(f"    [BA] {version}: response is not JSON: {resp.text[:120]!r}")
    return None


def fetch_ba_jobs(max_jobs: int = 50) -> List[Dict[str, Any]]:
    """Fetch jobs from the Bundesagentur für Arbeit job search API."""
    jobs: List[Dict[str, Any]] = []
    seen: set = set()

    for was, wo, umkreis in BA_QUERIES:
        if len(jobs) >= max_jobs:
            break
        print(f"[BA] Query: '{was}' in {wo} (radius {umkreis}km)")
        data = _ba_request({"was": was, "wo": wo, "umkreis": umkreis, "page": 1, "size": 50})
        if data is None:
            continue
        parsed = parse_ba_response(data)
        kept = 0
        for job in parsed:
            if len(jobs) >= max_jobs:
                break
            key = job.get("ba_refnr") or job["url"]
            if key in seen or job["url"] in seen:
                continue
            seen.update({key, job["url"]})
            if not is_relevant_title(job["job_title"])[0]:
                continue
            jobs.append(job)
            kept += 1
            print(f"    [BA] {job['company']} | {job['job_title']} | {job['location']}")
        print(f"    [BA] {data.get('maxErgebnisse', '?')} hits, {len(parsed)} usable on page 1, {kept} new technical roles kept")

    return jobs


# ── Source 2: Arbeitnow Public API ───────────────────────────────────────────

def fetch_arbeitnow_jobs(max_jobs: int = 50) -> List[Dict[str, Any]]:
    """Fetch jobs from Arbeitnow API and filter for German/tech roles."""
    jobs: List[Dict[str, Any]] = []
    seen_urls: set = set()

    try:
        print(f"[Arbeitnow] Fetching job board API...")
        resp = requests.get(
            ARBEITNOW_API_URL,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"    [Arbeitnow] HTTP {resp.status_code} - skipping")
            return jobs

        data = resp.json()
        jobs_list = data.get("data", [])
        if not isinstance(jobs_list, list):
            jobs_list = []

        for job in jobs_list:
            if len(jobs) >= max_jobs:
                break

            url = job.get("url", "")
            if not url or url in seen_urls:
                continue

            title = job.get("title", "Unknown")
            company = job.get("company_name", "Unknown")
            location = job.get("location", "Unknown")
            description = job.get("description", "") or ""

            # Filter for relevant keywords/locations
            combined_text = f"{title} {company} {location} {description}".lower()
            if not any(_has_word(combined_text, kw) for kw in ARBEITNOW_KEYWORDS):
                continue
            if not any(_has_word(combined_text, loc) for loc in ARBEITNOW_LOCATIONS):
                continue

            jobs.append(normalize_url_entry(url, company, title, location, "Arbeitnow"))
            seen_urls.add(url)
            print(f"    [Arbeitnow] {company} | {title} | {location}")

    except Exception as exc:
        print(f"    [Arbeitnow] Error: {exc}")

    return jobs


def normalize_url_entry(url: str, company: str, job_title: str, location: str, source: str) -> Dict[str, Any]:
    """Normalize a job entry into targets.json schema."""
    return {
        "url": url.strip() if url else "",
        "company": company.strip() if company else "Unknown",
        "job_title": job_title.strip() if job_title else "Unknown",
        "location": location.strip() if location else "Unbekannt",
        "source": source,
    }


# ── Source 3: Stellenwerk Dortmund (Playwright) ─────────────────────────────

def scrape_stellenwerk(page) -> List[Dict[str, Any]]:
    """Scrape Stellenwerk Dortmund job board."""
    jobs: List[Dict[str, Any]] = []
    seen_urls: set = set()

    try:
        print(f"[Stellenwerk] Navigating to Stellenwerk Dortmund...")
        page.goto(STELLENWERK_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        # Accept cookies if dialog appears
        try:
            cookie_btn = page.locator("button:has-text('Akzeptieren'), button:has-text('OK'), button:has-text('Einverstanden'), button:has-text('Alle akzeptieren')").first
            if cookie_btn and cookie_btn.is_visible():
                cookie_btn.click()
                page.wait_for_timeout(1000)
        except Exception:
            pass

        # Extract job listing links
        anchors = page.locator("a[href*='job'], a[href*='stellenangebot'], a[href*='stelle']")
        count = anchors.count()
        print(f"    [Stellenwerk] Found {count} links on page")

        collected_urls: List[str] = []
        for i in range(min(count, 30)):  # Limit to 30 per page
            try:
                el = anchors.nth(i)
                href = el.get_attribute("href")
                if not href:
                    continue
                if href.startswith("/"):
                    href = "https://www.stellenwerk.de" + href
                if "stellenwerk.de" not in href:
                    continue
                if re.search(r"/(account|login|register|book)(/|$)", href):
                    continue  # employer/booking pages, not job postings
                if href in collected_urls:
                    continue
                collected_urls.append(href)
            except Exception:
                continue

        # Visit each job detail page
        for url in collected_urls[:20]:  # Limit to 20 detail pages
            if len(jobs) >= 50:
                break
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(1500)

                title_el = page.locator("h1").first
                title = title_el.inner_text().strip()[:100] if title_el and title_el.is_visible() else "Unknown"

                company_el = page.locator("h2, .company-name, .firma, [data-testid='company-name']").first
                company = company_el.inner_text().strip()[:100] if company_el and company_el.is_visible() else "Unknown"

                location_el = page.locator(".ort, .location, .standort, [data-testid='location']").first
                location = location_el.inner_text().strip()[:50] if location_el and location_el.is_visible() else "Dortmund"

                jobs.append(normalize_url_entry(url, company, title, location, "Stellenwerk"))
                seen_urls.add(url)
                print(f"    [Stellenwerk] {company} | {title} | {location}")

            except Exception:
                continue

    except Exception as exc:
        print(f"    [Stellenwerk] Error: {exc}")

    return jobs


# ── Source 4: StepStone fallback (Playwright) ───────────────────────────────

def scrape_stepstone(page, max_jobs: int = 20) -> List[Dict[str, Any]]:
    """Scrape StepStone as fallback source."""
    jobs: List[Dict[str, Any]] = []
    seen_urls: set = set()

    STEPSTONE_QUERIES = [
        ("Werkstudent Softwareentwicklung", "Dortmund"),
        ("Werkstudent Informatik", "Dortmund"),
        ("Werkstudent KI Maschinelles Lernen", "Dortmund"),
    ]

    for query, location in STEPSTONE_QUERIES:
        if len(jobs) >= max_jobs:
            break

        try:
            print(f"[StepStone] Searching: {query} in {location}")
            query_encoded = query.replace(" ", "-").lower()
            location_encoded = location.replace(" ", "-").lower()
            search_url = f"https://www.stepstone.de/jobs/{query_encoded}/{location_encoded}"

            page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            anchors = page.locator("a[href*='stellenangebote--']")
            count = anchors.count()
            collected_urls: List[str] = []
            for i in range(count):
                href = anchors.nth(i).get_attribute("href")
                if href and "stellenangebote--" in href:
                    if href.startswith("/"):
                        href = "https://www.stepstone.de" + href
                    if href not in collected_urls:
                        collected_urls.append(href)

            for url in collected_urls[:10]:
                if len(jobs) >= max_jobs:
                    break
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(1500)

                    title_el = page.locator("h1").first
                    title = title_el.inner_text().strip()[:100] if title_el and title_el.is_visible() else "Unknown"

                    company_selectors = [
                        '[data-at="job-header-company-name"]',
                        '[data-qa="company-name"]',
                        '.job-header__company-name',
                        '.company-name',
                    ]
                    company = "Unknown"
                    for sel in company_selectors:
                        el = page.locator(sel).first
                        if el and el.is_visible():
                            company = el.inner_text().strip()[:100]
                            break

                    location_el = page.locator('[data-at="job-header-location"], .job-header__location, .location').first
                    location = location_el.inner_text().strip()[:50] if location_el and location_el.is_visible() else "Unbekannt"

                    jobs.append(normalize_url_entry(url, company, title, location, "StepStone"))
                    print(f"    [StepStone] {company} | {title} | {location}")

                except Exception:
                    continue

        except Exception as exc:
            print(f"    [StepStone] Error for '{query}': {exc}")

    return jobs


# ── Main orchestrator ───────────────────────────────────────────────────────

def scrape_all_jobs(headless: bool = True, max_jobs: int = 50) -> List[Dict[str, Any]]:
    """Run all discovery sources and return new, deduplicated jobs."""
    existing_targets = load_existing_targets()
    history = load_history()

    existing_urls: set = {t.get("url", "") for t in existing_targets}
    history_urls: set = {u for u in history.keys()} if isinstance(history, dict) else set()

    new_targets: List[Dict[str, Any]] = []
    seen_urls: set = set(existing_urls) | set(history_urls)

    print(f"[scraper] Existing targets: {len(existing_targets)}")
    print(f"[scraper] Already applied (history): {len(history_urls)}")
    print(f"[scraper] Starting multi-source discovery...")

    # Source 1: Bundesagentur für Arbeit API
    print(f"\n[scraper] === Source 1: Bundesagentur für Arbeit API ===")
    ba_jobs = fetch_ba_jobs(max_jobs=max(1, (max_jobs * 2) // 3))  # local source gets the largest share
    for job in ba_jobs:
        if len(new_targets) >= max_jobs:
            break
        if is_duplicate(job["url"], existing_urls, seen_urls):
            continue
        new_targets.append(job)
        seen_urls.add(job["url"])
    print(f"[scraper] BA-API contributed {len([j for j in new_targets if j.get('source') == 'BA-API'])} jobs")

    # Source 2: Arbeitnow API
    print(f"\n[scraper] === Source 2: Arbeitnow API ===")
    an_jobs = _technical_only(fetch_arbeitnow_jobs(max_jobs=max_jobs), "Arbeitnow")[: max(1, max_jobs // 3)]
    for job in an_jobs:
        if len(new_targets) >= max_jobs:
            break
        if is_duplicate(job["url"], existing_urls, seen_urls):
            continue
        new_targets.append(job)
        seen_urls.add(job["url"])
    print(f"[scraper] Arbeitnow contributed {len([j for j in new_targets if j.get('source') == 'Arbeitnow'])} jobs")

    # Source 3: Stellenwerk + StepStone (Playwright)
    print(f"\n[scraper] === Source 3: Stellenwerk Dortmund + StepStone (Playwright) ===")
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="de-DE",
            )
            page = context.new_page()

            try:
                stellenwerk_jobs = _technical_only(scrape_stellenwerk(page), "Stellenwerk")
                for job in stellenwerk_jobs:
                    if len(new_targets) >= max_jobs:
                        break
                    if is_duplicate(job["url"], existing_urls, seen_urls):
                        continue
                    new_targets.append(job)
                    seen_urls.add(job["url"])
                print(f"[scraper] Stellenwerk contributed {len([j for j in new_targets if j.get('source') == 'Stellenwerk'])} jobs")

                stepstone_jobs = _technical_only(scrape_stepstone(page, max_jobs=max_jobs // 3), "StepStone")
                for job in stepstone_jobs:
                    if len(new_targets) >= max_jobs:
                        break
                    if is_duplicate(job["url"], existing_urls, seen_urls):
                        continue
                    new_targets.append(job)
                    seen_urls.add(job["url"])
                print(f"[scraper] StepStone contributed {len([j for j in new_targets if j.get('source') == 'StepStone'])} jobs")

            finally:
                browser.close()
    except Exception as exc:
        print(f"[scraper] Playwright error: {exc}")

    return new_targets


def load_history() -> Dict[str, Any]:
    """Load history.json."""
    if not HISTORY_PATH.exists():
        return {}
    with HISTORY_PATH.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def update_targets_file(headless: bool = True, max_jobs: int = 50) -> Dict[str, int]:
    """
    Run the multi-source scraper and merge new targets into targets.json.
    Returns a dict with counts: {'existing': N, 'new': M, 'total': K}.
    """
    existing = load_existing_targets()
    existing_count = len(existing)

    print(f"[scraper] Existing targets in targets.json: {existing_count}")
    print(f"[scraper] Starting multi-source job discovery...")

    new_jobs = scrape_all_jobs(headless=headless, max_jobs=max_jobs)

    if new_jobs:
        all_targets = existing + new_jobs
        save_targets(all_targets)
        print(f"\n[scraper] Added {len(new_jobs)} new targets to targets.json")
        print(f"[scraper] Total targets: {len(all_targets)}")
    else:
        print(f"\n[scraper] No new targets found.")
        all_targets = existing

    return {
        "existing": existing_count,
        "new": len(new_jobs),
        "total": len(all_targets),
    }


def load_existing_targets() -> List[Dict[str, Any]]:
    """Load existing targets from targets.json."""
    if not TARGETS_PATH.exists():
        return []
    # Raise on unreadable/corrupt JSON: returning [] here would make
    # update_targets_file() overwrite targets.json with only the new jobs.
    with TARGETS_PATH.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("targets", "jobs", "items"):
            if isinstance(data.get(key), list):
                return data[key]
    raise ValueError("targets.json must contain a JSON array")


def main() -> int:
    parser = argparse.ArgumentParser(description="Job Applier - Multi-Source Job Scraper")
    parser.add_argument("--headless", action="store_true", default=True, help="Run browser headless")
    parser.add_argument("--headed", action="store_true", help="Run browser with visible window")
    parser.add_argument("--max-jobs", type=int, default=50, help="Max new jobs to discover")
    args = parser.parse_args()

    headless = args.headless and not args.headed

    result = update_targets_file(headless=headless, max_jobs=args.max_jobs)

    print(f"\n{'='*50}")
    print(f"SCRAPER SUMMARY:")
    print(f"  Existing targets: {result['existing']}")
    print(f"  New targets: {result['new']}")
    print(f"  Total targets: {result['total']}")
    print(f"{'='*50}")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
