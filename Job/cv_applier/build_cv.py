#!/usr/bin/env python3
"""
Standalone CV builder.

Reads a JSON payload file and generates a PDF CV using ReportLab
(embedded fonts, reliable rendering). No LaTeX required.

The .tex file is still generated for reference/debugging, but the PDF is
produced directly from the payload data — no fragile LaTeX text extraction.

Usage:
    python build_cv.py payload.json
    python build_cv.py payload.json --output-pdf out.pdf
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

# Ensure parent (Job/) is on sys.path so pdf_writer can be imported
_JOB_DIR = Path(__file__).resolve().parent.parent
if str(_JOB_DIR) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(_JOB_DIR))
from pdf_writer import write_pdf  # noqa: E402

DEFAULT_JSON_PATH = Path(__file__).with_name("cv_payload.json")
DEFAULT_OUTPUT_TEX = Path(__file__).with_name("output.tex")
DEFAULT_OUTPUT_PDF = Path(__file__).with_name("output.pdf")
DEFAULT_TEMPLATE_PATH = Path(__file__).with_name("template.tex")

REQUIRED_KEYS = [
    "summary",
    "sopra_bullet_1",
    "sopra_bullet_2",
    "tutor_bullet_1",
    "tutor_bullet_2",
    "thesis_bullet_1",
    "thesis_bullet_2",
    "formula_bullet_1",
    "formula_bullet_2",
    "birdclef_bullet_1",
    "birdclef_bullet_2",
]


def load_payload(payload_path: Path) -> Dict[str, Any]:
    """Load and validate the payload JSON file."""
    if not payload_path.exists():
        raise FileNotFoundError(f"Payload file not found: {payload_path}")

    with payload_path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)

    if not isinstance(payload, dict):
        raise ValueError("The payload JSON must be an object at the top level.")

    missing = [key for key in REQUIRED_KEYS if key not in payload]
    if missing:
        raise ValueError(f"Payload is missing required keys: {missing}")

    return payload


def sanitize_content(payload: Dict[str, Any]) -> Dict[str, str]:
    """Normalize JSON values for CV generation."""
    result: Dict[str, str] = {}

    for key in REQUIRED_KEYS:
        value = str(payload[key]).strip()
        if not value:
            raise ValueError(f"Payload key '{key}' is empty.")
        result[key] = value

    result["date"] = datetime.now().strftime("%d.%m.%Y")
    return result


def render_template(template_path: Path, content: Dict[str, str]) -> str:
    """Substitute placeholders into the CV template (.tex for reference)."""
    if not template_path.exists():
        raise FileNotFoundError(f"Template file not found: {template_path}")

    template_text = template_path.read_text(encoding="utf-8-sig")

    for key, value in content.items():
        template_text = template_text.replace(f"{{{{{key}}}}}", value)

    unfilled = re.findall(r"\{\{(\w+)\}\}", template_text)
    if unfilled:
        print(f"    WARNING: Unfilled placeholders detected: {sorted(set(unfilled))}")

    return template_text


def _build_cv_lines(content: Dict[str, str]) -> list:
    """Build clean text lines directly from the payload content (no LaTeX)."""
    lines = [
        "MALEK ALOUI",
        "",
        "Informatik – B.Sc. abgeschlossen (TU Dortmund)",
        "Maschinenbau & Robotik – M.Sc. (angestrebt, TU Dortmund)",
        "",
        "Kontakt",
        "E-Mail: malekaloui1777@gmail.com",
        "Telefon: +49 176 84243799",
        "Ort: Dortmund 44263, Deutschland",
        "",
        "Profil",
        content["summary"],
        "",
        "TU Dortmund – Tutor (Okt 2024 – Sep 2025)",
        f"  • {content['tutor_bullet_1']}",
        f"  • {content['tutor_bullet_2']}",
        "",
        "Projekte",
        "KI-Pipeline Kleinwasserfahrzeuge – Thesis (Apr 2025 – Aktuell)",
        f"  • {content['thesis_bullet_1']}",
        f"  • {content['thesis_bullet_2']}",
        "",
        "Batteriesysteme – Formel Student",
        f"  • {content['formula_bullet_1']}",
        f"  • {content['formula_bullet_2']}",
        "",
        "Biodiversitäts-Klassifikation – BirdCLEF",
        f"  • {content['birdclef_bullet_1']}",
        f"  • {content['birdclef_bullet_2']}",
        "",
        "Technische Fähigkeiten",
        "Python, C++, ROS 2, PyTorch, OpenCV, Docker, Kotlin, Git, SQL, Linux",
        "",
        "Sprachen",
        "Deutsch (Muttersprache), Englisch (fließend)",
        "",
        f"Erstellt am: {content['date']}",
    ]
    return lines


def compile_pdf(tex_path: Path, pdf_path: Path, content: Dict[str, str]) -> None:
    """Generate the CV PDF directly from payload content.

    The .tex file is kept for reference, but the PDF is produced from the
    structured payload data — no fragile LaTeX text extraction.
    """
    if not tex_path.exists():
        raise FileNotFoundError(f"LaTeX source file not found: {tex_path}")

    # Build clean text lines directly from payload (bypass LaTeX entirely)
    text_lines = _build_cv_lines(content)

    try:
        write_pdf(text_lines, pdf_path)
        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            print(f"SUCCESS: '{pdf_path}' generated successfully.")
            return
    except Exception as exc:
        print(f"    WARNING: PDF generation error: {exc}")

    raise RuntimeError("PDF compilation failed.")


def build_cv(payload_path: Path, template_path: Path, output_tex: Path, output_pdf: Path) -> int:
    """Build the CV PDF from a JSON payload file."""
    payload = load_payload(payload_path)
    content = sanitize_content(payload)

    # Generate .tex for reference/debugging
    tex_content = render_template(template_path, content)
    output_tex.write_text(tex_content, encoding="utf-8")

    # Generate PDF directly from payload (no LaTeX text extraction)
    compile_pdf(output_tex, output_pdf, content)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone CV builder from JSON payload to PDF.")
    parser.add_argument("payload", nargs="?", default=None, help="Path to the JSON payload file.")
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE_PATH), help="Path to the LaTeX template.")
    parser.add_argument("--output-tex", default=str(DEFAULT_OUTPUT_TEX), help="Path for the rendered .tex file.")
    parser.add_argument("--output-pdf", default=str(DEFAULT_OUTPUT_PDF), help="Path for the generated PDF.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    payload_arg = args.payload or os.environ.get("BUILD_CV_JSON") or str(DEFAULT_JSON_PATH)
    payload_path = Path(payload_arg).expanduser().resolve()
    template_path = Path(args.template).expanduser().resolve()
    output_tex_path = Path(args.output_tex).expanduser().resolve()
    output_pdf_path = Path(args.output_pdf).expanduser().resolve()

    try:
        return build_cv(payload_path, template_path, output_tex_path, output_pdf_path)
    except Exception as exc:
        print(f"FATAL ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
