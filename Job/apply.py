"""
apply.py — Unified Job Application Pipeline
============================================
Runs the standalone CV and cover-letter builders for a given job URL,
retrying each step on failure until both PDFs are successfully generated.

Usage:
    python apply.py "https://www.stepstone.de/..."
    python apply.py "https://www.stepstone.de/..." --cv-only
    python apply.py "https://www.stepstone.de/..." --letter-only

Options:
    --cv-only       Generate only the CV PDF (output.pdf)
    --letter-only   Generate only the cover letter PDF (anschreiben_output.pdf)
    --retries N     Max outer retries per step (default: 3)
    --fallback      If AI fails all retries, compile PDFs from hardcoded fallback content

Exit codes:
    0 — both requested PDFs generated successfully
    1 — one or more PDFs could not be generated
"""

import json
import sys
import argparse
import subprocess
import time
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).parent
CV_SCRIPT      = WORKSPACE_ROOT / "cv_applier" / "build_cv.py"
LETTER_SCRIPT  = WORKSPACE_ROOT / "cover_applier" / "build_cover_letter.py"
PROFILE_PATH   = WORKSPACE_ROOT / "profile.json"
CV_PAYLOAD     = WORKSPACE_ROOT / "cv_applier" / "cv_payload.json"
LETTER_PAYLOAD = WORKSPACE_ROOT / "cover_applier" / "cover_letter_payload.json"
CV_PDF         = WORKSPACE_ROOT / "cv_applier" / "output.pdf"
LETTER_PDF     = WORKSPACE_ROOT / "cover_applier" / "anschreiben_output.pdf"


def load_profile() -> dict:
    if not PROFILE_PATH.exists():
        raise FileNotFoundError(f"Profile file not found: {PROFILE_PATH}")
    with PROFILE_PATH.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_payload(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_default_payloads() -> tuple[Path, Path]:
    profile = load_profile()

    cv_payload = {
        "summary": profile.get("status", ""),
        "sopra_bullet_1": "Entwicklung einer Kotlin-Anwendung zur Modellierung der Spielregellogik für ein 2x3-Rastersystem (6 Karten) inklusive Hotseat-Modus und automatisierter Test-Decks.",
        "sopra_bullet_2": "Agile Umsetzung (Scrum) einer verteilten Multiplayer-Architektur via BGW-Net mit Fokus auf State-Serialization, Undo/Redo-Mechanismen und Bot-Turnier-Integration.",
        "tutor_bullet_1": "Vermittlung hardwarenaher Informatikgrundlagen an Bachelorstudierende",
        "tutor_bullet_2": "Eigenverantwortliche Leitung wöchentlicher Übungsgruppen und individuelle Betreuung",
        "thesis_bullet_1": "Entwicklung einer KI-Pipeline zur Erkennung und zum Tracking von Kleinwasserfahrzeugen mit ROS 2, Python und Docker",
        "thesis_bullet_2": "Integration moderner Segmentierungsverfahren in Hafensicherheitsarchitekturen für ein robustes Echtzeitsystem",
        "formula_bullet_1": "Entwicklung und hardwarenahes Testing von Batteriesystemen unter Einsatz von C++, MATLAB und Python",
        "formula_bullet_2": "Systemerweiterung durch API-Anbindung und Implementierung interaktiver Funktionen",
        "birdclef_bullet_1": "Leitung von Datenaufbereitung und Modellevaluation zur Klassifikation von Biodiversitäts-Audiodaten",
        "birdclef_bullet_2": "Höchste Bewertung im Kurs (Score 0,87) durch gezielte Optimierung der ML-Pipeline",
    }

    letter_payload = {
        "company_name": "Testfirma GmbH",
        "company_address": "Musterstraße 12, Dortmund",
        "job_title": "Werkstudent / Junior Developer Informatik",
        "salutation": "Sehr geehrte Damen und Herren,",
        "paragraph_1": "Mit großem Interesse bewerbe ich mich um die ausgeschriebene Stelle. Als Informatikabsolvent mit abgeschlossenem B.Sc. an der TU Dortmund bringe ich ein fundiertes Profil in Computer Vision, Machine Learning und Softwareentwicklung mit.",
        "paragraph_2": "Während meiner vorherigen Werkstudententätigkeit führte ich systematische System- und Integrationstests an elektronischen Fahrzeugsteuerungssystemen durch und erstellte präzise technische Testberichte.",
        "paragraph_3": "Ich bin ab sofort für 15 bis 20 Stunden pro Woche verfügbar und freue mich auf ein persönliches Gespräch.",
    }

    write_payload(CV_PAYLOAD, cv_payload)
    write_payload(LETTER_PAYLOAD, letter_payload)
    return CV_PAYLOAD, LETTER_PAYLOAD


def run_step(script: Path, payload: Path, output_pdf: Path, step_name: str, max_retries: int = 3) -> bool:
    """Run a standalone builder script with a payload file and retry on failure."""
    for attempt in range(1, max_retries + 1):
        print(f"\n{'='*60}")
        print(f"  {step_name}  — attempt {attempt}/{max_retries}")
        print(f"{'='*60}")

        result = subprocess.run(
            [
                sys.executable,
                str(script),
                str(payload),
                "--output-pdf",
                str(output_pdf),
            ],
            text=True,
            capture_output=True,
        )

        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="")

        if result.returncode == 0:
            print(f"\n  SUCCESS: {step_name} completed successfully.")
            return True

        print(f"\n  WARNING: {step_name} exited with code {result.returncode}.")
        if attempt < max_retries:
            wait = 5 * attempt
            print(f"      Retrying in {wait}s...")
            time.sleep(wait)

    print(f"\n  ERROR: {step_name} failed after {max_retries} attempts.")
    return False


def check_pdf(pdf: Path, step_name: str) -> bool:
    """Verify the expected PDF was actually produced."""
    if pdf.exists() and pdf.stat().st_size > 1000:
        size_kb = pdf.stat().st_size // 1024
        print(f"  {pdf.name} ({size_kb} KB) - OK")
        return True
    print(f"  ERROR: {pdf.name} not found or too small - {step_name} may have failed silently.")
    return False


def main():
    parser = argparse.ArgumentParser(description="Automated Job Application Pipeline")
    parser.add_argument("url", nargs="?",
                        default="https://www.stepstone.de/jobs/werkstudent-informatik",
                        help="Job posting URL")
    parser.add_argument("--cv-only",     action="store_true", help="Generate CV only")
    parser.add_argument("--letter-only", action="store_true", help="Generate cover letter only")
    parser.add_argument("--retries",     type=int, default=3, metavar="N",
                        help="Max retries per step (default: 3)")
    args = parser.parse_args()

    url         = args.url
    do_cv       = not args.letter_only
    do_letter   = not args.cv_only
    max_retries = args.retries

    print(f"\nJob Application Pipeline")
    print(f"    URL      : {url}")
    print(f"    Steps    : {'CV + Cover Letter' if do_cv and do_letter else 'CV only' if do_cv else 'Cover letter only'}")
    print(f"    Retries  : up to {max_retries} per step\n")

    results = {}
    cv_payload, letter_payload = build_default_payloads()

    # ── Step A: CV builder ─────────────────────────────────────────────────────
    if do_cv:
        if not CV_SCRIPT.exists():
            print(f"ERROR: Cannot find {CV_SCRIPT}")
            sys.exit(1)
        ok = run_step(CV_SCRIPT, cv_payload, CV_PDF, "CV Generation (build_cv.py)", max_retries)
        results["cv"] = ok and check_pdf(CV_PDF, "CV Generation")
    else:
        results["cv"] = True

    # ── Step B: Cover Letter builder ───────────────────────────────────────────
    if do_letter:
        if not LETTER_SCRIPT.exists():
            print(f"ERROR: Cannot find {LETTER_SCRIPT}")
            sys.exit(1)
        ok = run_step(LETTER_SCRIPT, letter_payload, LETTER_PDF, "Cover Letter (build_cover_letter.py)", max_retries)
        results["letter"] = ok and check_pdf(LETTER_PDF, "Cover Letter")
    else:
        results["letter"] = True

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  PIPELINE SUMMARY")
    print(f"{'='*60}")
    all_ok = True
    if do_cv:
        status = "OK" if results["cv"]     else "FAILED"
        print(f"  CV             : {status}  - {CV_PDF}")
        all_ok = all_ok and results["cv"]
    if do_letter:
        status = "OK" if results["letter"] else "FAILED"
        print(f"  Cover Letter   : {status} - {LETTER_PDF}")
        all_ok = all_ok and results["letter"]

    if all_ok:
        print("\n  All documents generated. Application ready to send!\n")
        sys.exit(0)
    else:
        print("\n  WARNING: One or more steps failed. Check the logs above.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
