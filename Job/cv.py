#!/usr/bin/env python3
"""Generate and compile the three static CV variants into PDFs.

Usage:
    python cv.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent
BUILD_STATIC_SCRIPT = WORKSPACE_ROOT / "build_static_cvs.py"
CV_DIR = WORKSPACE_ROOT / "cv_applier"
TARGETS = [
    CV_DIR / "cv_duales_studium.tex",
    CV_DIR / "cv_vollzeit.tex",
    CV_DIR / "cv_werkstudent.tex",
]


def ensure_pdflatex() -> str:
    pdflatex = shutil.which("pdflatex")
    if not pdflatex:
        raise RuntimeError("pdflatex is not available on PATH.")
    return pdflatex


def run_static_builder() -> None:
    print("[1/2] Regenerating static CV .tex variants...")
    result = subprocess.run(
        [sys.executable, str(BUILD_STATIC_SCRIPT)],
        cwd=WORKSPACE_ROOT,
        text=True,
        capture_output=True,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")
    if result.returncode != 0:
        raise RuntimeError("build_static_cvs.py failed.")


def compile_cvs(pdflatex_path: str) -> None:
    print("[2/2] Compiling static CV PDFs...")
    for tex_path in TARGETS:
        if not tex_path.exists():
            raise FileNotFoundError(f"Missing source file: {tex_path}")

        result = subprocess.run(
            [pdflatex_path, "-interaction=nonstopmode", "-output-directory", str(CV_DIR), str(tex_path)],
            cwd=WORKSPACE_ROOT,
            text=True,
            capture_output=True,
        )

        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="")

        if result.returncode != 0:
            raise RuntimeError(f"pdflatex failed for {tex_path.name} with code {result.returncode}.")

    print("All static CV PDFs generated successfully.")


def main() -> int:
    pdflatex_path = ensure_pdflatex()
    run_static_builder()
    compile_cvs(pdflatex_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
