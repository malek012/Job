#!/usr/bin/env python3
"""
Standalone cover-letter builder.

Generates a DIN 5008-compliant German cover letter PDF using LaTeX,
matching the exact styling of the CV template (same accent color, fonts, margins).
Uses pdflatex for compilation; falls back to reportlab only if pdflatex is unavailable.

Usage:
    python build_cover_letter.py payload.json
    python build_cover_letter.py payload.json --output-pdf out.pdf
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

# Ensure parent (Job/) is on sys.path so pdf_writer can be imported
_JOB_DIR = Path(__file__).resolve().parent.parent
if str(_JOB_DIR) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(_JOB_DIR))
from pdf_writer import write_pdf  # noqa: E402

DEFAULT_JSON_PATH = Path(__file__).with_name("cover_letter_payload.json")
DEFAULT_OUTPUT_TEX = Path(__file__).with_name("anschreiben.tex")
DEFAULT_OUTPUT_PDF = Path(__file__).with_name("Anschreiben.pdf")
DEFAULT_TEMPLATE_PATH = Path(__file__).with_name("anschreiben_template.tex")

REQUIRED_KEYS = [
    "company_name",
    "job_title",
    "salutation",
    "paragraph_1",
    "paragraph_2",
    "paragraph_3",
]
OPTIONAL_KEYS = [
    "company_address",
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
    """Normalize JSON values for cover letter generation."""
    result: Dict[str, str] = {}

    for key in REQUIRED_KEYS:
        value = str(payload[key]).strip()
        if not value:
            raise ValueError(f"Payload key '{key}' is empty.")
        result[key] = value

    # Optional keys: always include (even if empty) so LaTeX placeholders get replaced
    for key in OPTIONAL_KEYS:
        value = str(payload.get(key, "")).strip()
        result[key] = value

    result["date"] = datetime.now().strftime("%d.%m.%Y")

    # Profile constraint: cover letters must feature only the completed B.Sc.
    # in Computer Science at TU Dortmund. Zero Master's references allowed.
    for key in REQUIRED_KEYS + OPTIONAL_KEYS:
        if re.search(r"\bmaster\b", result.get(key, ""), re.I):
            raise ValueError(
                f"Cover letter field '{key}' must not mention Master's studies. "
                f"Profile is B.Sc. Informatik (TU Dortmund) only."
            )

    return result


def render_template(template_path: Path, content: Dict[str, str]) -> str:
    """Substitute placeholders into the cover-letter LaTeX template."""
    if not template_path.exists():
        raise FileNotFoundError(f"Template file not found: {template_path}")

    template_text = template_path.read_text(encoding="utf-8-sig")

    for key, value in content.items():
        template_text = template_text.replace(f"{{{{{key}}}}}", value)

    # Remove lines that still contain unfilled optional placeholders
    unfilled = re.findall(r"\{\{(\w+)\}\}", template_text)
    if unfilled:
        print(f"    WARNING: Unfilled placeholders detected: {sorted(set(unfilled))}")
        lines = template_text.split("\n")
        filtered = []
        for line in lines:
            if not re.search(r"\{\{\w+\}\}", line):
                filtered.append(line)
        template_text = "\n".join(filtered)

    return template_text


_LATEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
    "{": r"\{", "}": r"\}",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}
_LATEX_SPECIALS_RE = re.compile("|".join(re.escape(k) for k in _LATEX_SPECIALS))


def _latex_escape_text(value: str) -> str:
    """Escape LaTeX special characters in plain text, in ONE pass.

    (Replacing them one after another escaped the backslashes of earlier
    escapes again: '&' became '\\textbackslash{}&', LaTeX failed, and the
    builder silently produced a plain-text letter.)
    """
    return _LATEX_SPECIALS_RE.sub(lambda m: _LATEX_SPECIALS[m.group(0)], value)


def _escape_latex_content(content: Dict[str, str]) -> Dict[str, str]:
    """Escape all string values in content for LaTeX."""
    return {k: _latex_escape_text(v) for k, v in content.items()}


def compile_pdf(tex_path: Path, pdf_path: Path) -> None:
    """Compile the LaTeX file into a PDF using pdflatex.

    Falls back to reportlab only if pdflatex is unavailable or fails.
    """
    if not tex_path.exists():
        raise FileNotFoundError(f"LaTeX source file not found: {tex_path}")
    # Never let a PDF from an earlier run survive a failed compile.
    if pdf_path.exists():
        pdf_path.unlink()

    # Try pdflatex first (produces proper LaTeX output matching CV template)
    try:
        result = subprocess.run(
            [
                "pdflatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                str(tex_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(tex_path.parent),
        )
    except FileNotFoundError:
        result = None

    if result is not None and result.returncode == 0:
        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            print(f"SUCCESS: '{pdf_path}' generated with pdflatex.")
            return
        # pdflatex ran but didn't produce PDF, fall through

    if os.getenv("JOB_APPLIER_ALLOW_PLAIN_LETTER", "0") != "1":
        reason = "pdflatex not found" if result is None else _latex_error(tex_path, result)
        raise RuntimeError(f"Cover letter could not be typeset ({reason}). No PDF was written.")

    # Plain-text fallback: only when explicitly allowed (never for real applications)
    print("    NOTE: pdflatex unavailable or failed; using reportlab PDF fallback.")
    tex_text = tex_path.read_text(encoding="utf-8", errors="ignore")
    import re as _re

    # Strip LaTeX commands for fallback text
    lines = []
    for raw in tex_text.splitlines():
        line = raw.strip()
        if line.startswith("%") or not line:
            continue
        line = _re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?(\{[^}]*\})?", " ", line)
        line = line.replace("{", " ").replace("}", " ")
        line = _re.sub(r"\s+", " ", line).strip()
        if line and len(line) > 1:
            lines.append(line)

    _write_pdf_fallback(lines or ["Cover Letter"], pdf_path)
    if pdf_path.exists() and pdf_path.stat().st_size > 0:
        print(f"SUCCESS: '{pdf_path}' generated (reportlab fallback).")
        return

    raise RuntimeError("PDF compilation failed.")


def _latex_error(tex_path: Path, result: Any) -> str:
    """First LaTeX error line from the .log (or the process output)."""
    log_path = tex_path.with_suffix(".log")
    text = log_path.read_text(encoding="utf-8", errors="ignore") if log_path.exists() else (result.stdout or "")
    for line in text.splitlines():
        if line.startswith("!"):
            return line[:160]
    return f"pdflatex exit code {result.returncode}"


def _write_pdf_fallback(lines: list, output_path: Path) -> None:
    """Write PDF using reportlab fallback."""
    from pdf_writer import write_pdf as _write_pdf_func

    _write_pdf_func(lines, output_path)


def build_cover_letter(payload_path: Path, template_path: Path, output_tex: Path, output_pdf: Path) -> int:
    """Build the cover-letter PDF from a JSON payload file."""
    payload = load_payload(payload_path)
    content = sanitize_content(payload)

    # Escape content for LaTeX
    content = {k: _latex_escape_text(v) for k, v in content.items()}

    # Generate .tex for reference/debugging
    tex_content = render_template(template_path, content)
    output_tex.write_text(tex_content, encoding="utf-8")

    # Generate PDF via pdflatex (or reportlab fallback)
    compile_pdf(output_tex, output_pdf)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone cover-letter builder from JSON payload to PDF.")
    parser.add_argument("payload", nargs="?", default=None, help="Path to the JSON payload file.")
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE_PATH), help="Path to the LaTeX template.")
    parser.add_argument("--output-tex", default=str(DEFAULT_OUTPUT_TEX), help="Path for the rendered .tex file.")
    parser.add_argument("--output-pdf", default=str(DEFAULT_OUTPUT_PDF), help="Path for the generated PDF.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    payload_arg = args.payload or os.environ.get("BUILD_COVER_LETTER_JSON") or str(DEFAULT_JSON_PATH)
    payload_path = Path(payload_arg).expanduser().resolve()
    template_path = Path(args.template).expanduser().resolve()
    output_tex_path = Path(args.output_tex).expanduser().resolve()
    output_pdf_path = Path(args.output_pdf).expanduser().resolve()

    try:
        return build_cover_letter(payload_path, template_path, output_tex_path, output_pdf_path)
    except Exception as exc:
        print(f"FATAL ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
