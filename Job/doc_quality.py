"""
doc_quality.py - checks that generated documents are the real, typeset versions.

A cover letter must be a pdfLaTeX-typeset PDF. The builder used to fall back
silently to a bare plain-text PDF when LaTeX failed (for example on a company
name with '&' or a job title with '#'); such letters must never be approved,
uploaded or e-mailed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

MIN_LETTER_BYTES = 8_000   # typeset letters are ~55-65 KB; the plain-text fallback is ~2 KB


def letter_problem(path: Optional[Path]) -> Optional[str]:
    """None if the cover letter is fine, else a short explanation."""
    if path is None or not Path(path).is_file():
        return "cover letter PDF is missing"
    data = Path(path).read_bytes()
    if not data.startswith(b"%PDF-"):
        return "cover letter is not a PDF"
    if b"pdfTeX" not in data or len(data) < MIN_LETTER_BYTES:
        return "cover letter is the plain-text fallback, not the typeset version - regenerate it"
    return None
