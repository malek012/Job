#!/usr/bin/env python3
"""Generate the three static CV variants from the shared LaTeX template.

The workflow is intentionally deterministic:
1. Read the base template and JSON payload.
2. Perform a full placeholder substitution across the whole template.
3. Rewrite the exact header block for each static variant.
4. Write clean LaTeX source with real newline characters only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent
TEMPLATE_PATH = WORKSPACE_ROOT / "cv_applier" / "template.tex"
PAYLOAD_PATH = WORKSPACE_ROOT / "cv_applier" / "cv_payload.json"

VARIANTS = {
    "cv_applier/cv_duales_studium.tex": {
        "title": r"Informatik-Absolvent (B.Sc.) \quad\textbar\quad Computer Vision \& Machine Learning",
        "subtitle": r"B.Sc. abgeschlossen (Juli 2026) \quad\textbar\quad Master-Integration \quad\textbar\quad Duales Studium",
        "education": "Erfolgreicher Abschluss: Bachelor of Science",
        "profile_sentence": "Absolvent der Informatik (B.Sc.) an der TU Dortmund mit Fokus auf Computer Vision, Machine Learning und Softwareentwicklung. Ich suche eine Position im Rahmen eines dualen Studiums, um meine technischen und methodischen Fähigkeiten in einem strukturierten, praxisorientierten Umfeld weiterzuentwickeln.",
    },
    "cv_applier/cv_vollzeit.tex": {
        "title": r"Informatik-Absolvent (B.Sc.) \quad\textbar\quad Computer Vision \& Machine Learning",
        "subtitle": r"B.Sc. abgeschlossen (Juli 2026) \quad\textbar\quad Fokus: Softwareentwicklung \quad\textbar\quad Vollzeit",
        "education": "Erfolgreicher Abschluss: Bachelor of Science",
        "profile_sentence": "Absolvent der Informatik (B.Sc.) an der TU Dortmund mit Fokus auf Computer Vision, Machine Learning und Softwareentwicklung. Ich suche eine Vollzeitposition, um meine Kenntnisse in moderner Softwareentwicklung, KI-gestützten Systemen und robusten Architekturkonzepten gezielt einzubringen.",
    },
    "cv_applier/cv_werkstudent.tex": {
        "title": r"Absolvent \& Masterstudent \quad\textbar\quad Computer Vision \& Machine Learning",
        "subtitle": r"B.Sc. abgeschlossen (Juli 2026) \quad\textbar\quad Vorbereitung Master \quad\textbar\quad Werkstudent",
        "education": "B.Sc. erfolgreich abgeschlossen -- Vorbereitung auf Masterstudium",
        "profile_sentence": "Absolvent der Informatik (B.Sc.) an der TU Dortmund mit Fokus auf Computer Vision, Machine Learning und Softwareentwicklung. Ich suche eine Werkstudentenstelle, um meine akademische Grundlage mit praktischen Projekterfahrungen in einer flexiblen, teamorientierten Umgebung zu verknüpfen.",
    },
}

SO_PRA_DATE_OLD = "Okt 2024 -- Mär 2025"
SO_PRA_DATE_NEW = "Apr 2026 -- Juli 2026"
SO_PRA_DATE_BRACKETLESS = "Apr 2026 -- Juli 2026"
PROFILE_SUMMARY = "Absolvent der Informatik (B.Sc.) an der TU Dortmund mit Fokus auf Computer Vision, Machine Learning und Softwareentwicklung. Praxiserfahrung durch anspruchsvolle Systemarchitektur-Projekte mit ROS 2, Python, C++ und Kotlin."


def load_template() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8-sig")


def load_payload() -> dict:
    with PAYLOAD_PATH.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def inject_payload(template_text: str, payload: dict) -> str:
    """Replace every placeholder with the matching JSON payload value."""
    for key, value in payload.items():
        template_text = template_text.replace(f"{{{{{key}}}}}", str(value))

    unfilled = re.findall(r"\{\{(\w+)\}\}", template_text)
    if unfilled:
        raise ValueError(f"Unfilled placeholders remain: {sorted(set(unfilled))}")
    return template_text


def apply_global_overrides(template_text: str) -> str:
    """Apply the hardcoded profile summary and SoPra timeline overrides."""
    template_text = template_text.replace(
        "7. und letztes Fachsemester Informatik, TU Dortmund, B.Sc.-Abschluss 2026, angestrebter Master",
        PROFILE_SUMMARY,
    )
    template_text = template_text.replace(SO_PRA_DATE_OLD, SO_PRA_DATE_NEW)
    return template_text


def hardcode_sopra_dates(template_text: str) -> str:
    template_text = template_text.replace("[Start Month -- End Month]", SO_PRA_DATE_BRACKETLESS)
    template_text = template_text.replace("[Month Year -- Month Year]", SO_PRA_DATE_BRACKETLESS)
    return template_text


def get_header_block(template_text: str) -> str:
    """Locate the exact LaTeX minipage header block in the shared template."""
    pattern = re.compile(
        r"\\begin\{minipage\}\[t\]\{0\.64\\textwidth\}\n"
        r"  \\vspace\{0pt\}\n"
        r".*?"
        r"\\end\{minipage\}%\n\\hfill%",
        re.DOTALL,
    )
    match = pattern.search(template_text)
    if not match:
        raise ValueError("Unable to locate the LaTeX header block in the template.")
    return match.group(0)


def build_header_block(title: str, subtitle: str) -> str:
    template = """\\begin{minipage}[t]{0.64\\textwidth}
    \\vspace{0pt}
    {\\Huge\\bfseries\\color{accent} Malek Aloui}\\\\[3pt]
    {\\large\\textcolor{darkgray}{__TITLE__}}\\\\[2pt]
    {\\small\\textcolor{darkgray}{__SUBTITLE__}}\\\\[4pt]
    \\small
    \\textbf{Tel:} \\texttt{+49~176~84243799} \\quad
    \\textbf{E-Mail:} \\href{mailto:malekaloui1777@gmail.com}{\\texttt{malekaloui1777@gmail.com}}\\\\[2pt]
    \\textbf{Ort:} Dortmund~44263 \\quad
    \\textbf{GitHub:} \\href{https://github.com/malek012}{github.com/malek012}
\\end{minipage}%"""
    return template.replace("__TITLE__", title).replace("__SUBTITLE__", subtitle)


def build_variant_text(base_text: str, config: dict) -> str:
    text = base_text
    old_block = get_header_block(text)
    new_block = build_header_block(config["title"], config["subtitle"])
    text = text.replace(old_block, new_block, 1)

    text = text.replace(
        PROFILE_SUMMARY,
        config["profile_sentence"],
        1,
    )
    text = text.replace("Laufend", "Erfolgreich abgeschlossen (Juni 2026)", 1)
    text = text.replace(
        "7. Fachsemester (Abschluss 2026), angestrebter Master",
        config["education"],
        1,
    )

    return text


def main() -> None:
    template_text = load_template()
    payload = load_payload()

    rendered_template = inject_payload(template_text, payload)
    rendered_template = apply_global_overrides(rendered_template)
    rendered_template = hardcode_sopra_dates(rendered_template)

    for output_path, config in VARIANTS.items():
        variant_text = build_variant_text(rendered_template, config)
        out_file = WORKSPACE_ROOT / output_path
        out_file.write_text(variant_text, encoding="utf-8")
        print(f"WROTE {out_file}")


if __name__ == "__main__":
    main()
