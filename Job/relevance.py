#!/usr/bin/env python3
"""
relevance.py - domain gate for the pipeline.

Only technical roles in these areas get documents (and LLM tokens):
software engineering, machine learning / AI, computer vision, robotics,
embedded systems, AIT (assembly, integration & test) engineering.

The decision is made on the JOB TITLE only. Descriptions mention "software"
and "AI" far too often to be evidence of what the role is.

    is_relevant_title("Werkstudent KI (m|w|d)")            -> (True,  "ki")
    is_relevant_title("Account Executive DACH")            -> (False, "excluded: account executive")
    is_relevant_title("Werkstudent (m/w/d)")               -> (False, "no technical keyword in title")
"""

from __future__ import annotations

import re
from typing import Tuple

_W = "a-z0-9äöüß"


def _rx(pattern: str) -> "re.Pattern[str]":
    """Compile with word boundaries that understand umlauts."""
    return re.compile(rf"(?<![{_W}])(?:{pattern})(?![{_W}])", re.I)


# A title containing any of these is rejected, whatever else it says.
EXCLUDE = [
    ("sales",               _rx(r"sales|vertrieb\w*|verkauf\w*|au(ß|ss)endienst\w*|pre-?sales")),
    ("account executive",   _rx(r"account[\s-]*(executive|manager\w*|management)|key[\s-]*account\w*|account-?\s*&")),
    ("customer success",    _rx(r"customer[\s-]*(success|support|service|care|experience)|kunden(betreu|service|berat)\w*")),
    ("solutions/sales engineer", _rx(r"(solutions?|sales|pre-?sales)[\s-]*(engineer|architect|consultant)\w*")),
    ("marketing",           _rx(r"\w*marketing\w*|seo|content[\s-]*(creator|manager)|social[\s-]*media\w*|pr-?\w*manager")),
    ("business role",       _rx(r"business[\s-]*(development|developer|analyst|manager)|partner[\s-]*manager\w*|strategy|strategie\w*")),
    ("product/project management", _rx(r"produkt\w*manag\w*|product[\s-]*(manager|owner|management)|proje[ck]t\w*[\s-]*(manag\w*|leiter\w*|koordinat\w*)|scrum[\s-]*master")),
    ("finance/HR/legal",    _rx(r"finance|finanz\w*|buchhalt\w*|controll\w*|accounting|steuer\w*|recruit\w*|personal\w*|\bhr\b|human[\s-]*resources|legal|jurist\w*|einkauf\w*|procurement")),
    ("consulting",          _rx(r"consultant\w*|consulting|berater\w*|beratung")),
    ("design/UX",           _rx(r"ux|ui/ux|designer\w*|grafik\w*")),
    ("training course",     _rx(r"weiterbildung|umschulung|kurs|lehrgang|dozent\w*|trainer\w*|schulung")),
    ("non-technical work",  _rx(r"lager\w*|fahrer\w*|kassier\w*|reinigung\w*|pflege\w*|verk(ä|ae)ufer\w*|servicekraft|aushilfe|sachbearbeit\w*|assistenz|assistent\w*|sekret\w*|empfang\w*")),
]

# A title needs at least one of these.
INCLUDE = [
    ("software",        _rx(r"software\w*|softwareentwickl\w*|anwendungsentwickl\w*|web-?entwickl\w*|app-?entwickl\w*|datenbankentwickl\w*|systementwickl\w*")),
    ("developer",       _rx(r"entwickler\w*|entwickler(\*|:|_)?in\w*|developer|programmierer\w*|programmer|coder")),
    ("engineering role", _rx(r"(backend|front-?end|full-?stack|devops|cloud|platform|data|ml|ai|ki|test|qa|site reliability)[\s-]*(engineer\w*|entwickl\w*|developer)|sre")),
    ("informatik",      _rx(r"informatik\w*|fachinformatik\w*|computer science")),
    ("language/stack",  _rx(r"python|c\+\+|cpp|c#|\.net|java|kotlin|rust|golang|typescript|javascript|react|angular|node\.?js|sql|linux|docker|kubernetes")),
    ("machine learning", _rx(r"machine[\s-]*learning|maschinelles[\s-]*lernen|deep[\s-]*learning|reinforcement[\s-]*learning|ml|mlops|data[\s-]*scien\w*|nlp|llm")),
    ("ai",              _rx(r"ai|ki|k(ü|ue)nstliche[\s-]*intelligenz|artificial[\s-]*intelligence|genai|ki-\w+|ai-\w+")),
    ("computer vision", _rx(r"computer[\s-]*vision|bildverarbeitung|image[\s-]*processing|perception")),
    ("robotics",        _rx(r"robotik\w*|robotics?|robot\w*|ros\s?2?|autonom\w*|slam")),
    ("embedded",        _rx(r"embedded|firmware|mikrocontroller\w*|microcontroller\w*|fpga|hardwarenah\w*|steuerger(ä|ae)t\w*|echtzeit\w*|rtos")),
    ("ait engineering", _rx(r"ait|assembly,?\s*integration\s*(&|and|und)\s*test\w*|integrations?-?\s*(und|&|and)\s*test\w*")),
    ("automation",      _rx(r"automatisierung\w*|automation|rpa")),
]


def is_relevant_title(title: str) -> Tuple[bool, str]:
    """(relevant, reason). The reason names the keyword group that decided."""
    text = (title or "").strip().lower()
    if not text or text == "unknown":
        return False, "no title"
    for label, pattern in EXCLUDE:
        if pattern.search(text):
            return False, f"excluded: {label}"
    for label, pattern in INCLUDE:
        if pattern.search(text):
            return True, label
    return False, "no technical keyword in title"


if __name__ == "__main__":
    import sys
    for t in sys.argv[1:]:
        print(is_relevant_title(t), "|", t)
