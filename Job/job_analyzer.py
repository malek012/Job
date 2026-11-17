"""
job_analyzer.py - Smart Job Analysis Engine
===========================================
Analyzes discovered job descriptions, extracts key requirements & tech stacks,
and scores each job against the candidate profile (profile.json).

All LLM calls go through the NVIDIA NIM free-tier (provider "nvidia") configured
in web-ui/.env — no API keys are hardcoded here.

Usage:
    from job_analyzer import analyze_job_description
    result = analyze_job_description(job_url, job_description_text, profile)
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

WORKSPACE_ROOT = Path(__file__).resolve().parent
PROFILE_PATH = WORKSPACE_ROOT / "profile.json"
ANALYSIS_DIR = WORKSPACE_ROOT / "job_analysis"
ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(WORKSPACE_ROOT.parent / "web-ui" / ".env", override=False)

TECH_KEYWORDS = [
    "python", "java", "c++", "cpp", "c#", "javascript", "typescript", "kotlin",
    "rust", "go", "golang", "ruby", "php", "swift", "scala", "matlab",
    "react", "angular", "vue", "svelte", "html", "css", "next.js", "node.js",
    "nodejs", "express", "webpack", "vite", "django", "flask", "fastapi",
    "spring", "spring boot", "aws", "azure", "gcp", "docker", "kubernetes",
    "k8s", "terraform", "jenkins", "gitlab", "github actions", "ci/cd", "ci cd",
    "linux", "unix", "bash", "sql", "postgresql", "postgres", "mysql",
    "mongodb", "redis", "elasticsearch", "oracle", "sqlite", "cassandra",
    "machine learning", "ml", "deep learning", "ai", "tensorflow", "pytorch",
    "scikit-learn", "pandas", "numpy", "computer vision", "nlp", "ros", "ros 2",
    "pytest", "unittest", "junit", "selenium", "cypress", "playwright",
    "softwareentwicklung", "anwendungsentwicklung", "systemarchitektur",
    "datenbanken", "maschinelles lernen", "künstliche intelligenz",
]

EXPERIENCE_PATTERNS = [
    (r"\bwerkstudent\b", "Werkstudent"),
    (r"\bduales studium\b", "Duales Studium"),
    (r"\bjunior\b", "Junior"),
    (r"\bvollzeit\b", "Vollzeit"),
    (r"\bfull.?time\b", "Vollzeit"),
    (r"\binternship\b|\bpraktikum\b", "Internship / Praktikum"),
    (r"\bsenior\b", "Senior"),
    (r"\bleading\b|\blead\b", "Lead / Leading"),
]


def load_profile() -> Dict[str, Any]:
    if not PROFILE_PATH.exists():
        return {}
    with PROFILE_PATH.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def _extract_tech_stack(text: str) -> List[str]:
    text_lower = text.lower()
    found = [kw for kw in TECH_KEYWORDS if kw.lower() in text_lower]
    seen = set()
    result: List[str] = []
    for kw in found:
        key = kw.lower()
        if key not in seen:
            seen.add(key)
            result.append(kw)
    return result


def _detect_experience_level(text: str) -> str:
    text_lower = text.lower()
    for pattern, label in EXPERIENCE_PATTERNS:
        if re.search(pattern, text_lower):
            return label
    return "Unbekannt"


def _detect_job_type(text: str) -> str:
    text_lower = text.lower()
    if "werkstudent" in text_lower:
        return "Werkstudent"
    if any(kw in text_lower for kw in ["vollzeit", "full-time", "full time"]):
        return "Vollzeit"
    if "duales" in text_lower or "dual" in text_lower:
        return "Duales Studium"
    return "Unbekannt"


def _detect_location(text: str) -> Optional[str]:
    cities = [
        "dortmund", "berlin", "münchen", "munich", "hamburg", "köln", "cologne",
        "frankfurt", "stuttgart", "düsseldorf", "leipzig", "essen", "bremen",
        "dresden", "hannover", "nürnberg", "nuremberg", "bonn", "münster",
    ]
    text_lower = text.lower()
    for city in cities:
        if city in text_lower:
            return city.title()
    return None


def _llm_analyze(
    job_description: str,
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    profile_summary = json.dumps(profile, ensure_ascii=False, indent=2)
    system_prompt = (
        "You are an expert technical recruiter analyzing German job postings. "
        "Given a job description and a candidate profile, extract structured "
        "insights and compute a match score (0-100). Respond ONLY with valid JSON, "
        "no markdown fences, no extra text.\n\nJSON schema:\n"
        "{\n  \"tech_stack\": [\"skill1\"],\n  \"key_requirements\": [\"req1\"],\n"
        '  \"match_score\": 0-100,\n  \"experience_level\": "Werkstudent | Vollzeit | ...",\n'
        '  \"job_type\": "Werkstudent | Vollzeit | ...",\n  \"location\": "City or null",\n'
        '  \"summary": "2-3 sentence summary"\n}\n\n'
        f"CANDIDATE PROFILE:\n{profile_summary}\n\n"
        "Scoring: +40 skill overlap, +25 job type Werkstudent/Vollzeit, "
        "+20 Germany location, +15 final-semester fit."
    )
    user_prompt = f"JOB DESCRIPTION:\n{job_description}\n\nReturn the JSON analysis now."

    try:
        import os
        from src.utils import llm_provider

        llm = llm_provider.get_llm_model(
            provider="nvidia",
            model_name=os.getenv("DEFAULT_LLM_MODEL", "nvidia/nemotron-3-ultra-550b-a55b"),
            temperature=0.1,
            base_url=os.getenv("OPENAI_API_BASE", "https://integrate.api.nvidia.com/v1"),
            api_key=os.getenv("NVIDIA_API_KEY", ""),
        )
        if llm is None:
            raise RuntimeError("LLM initialization returned None")

        from langchain_core.messages import HumanMessage, SystemMessage
        messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        response = llm.invoke(messages)
        content = response.content if hasattr(response, "content") else str(response)
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        parsed = json.loads(content)
        parsed["tech_stack"] = list(dict.fromkeys(parsed.get("tech_stack", [])))
        parsed["key_requirements"] = list(dict.fromkeys(parsed.get("key_requirements", [])))
        parsed["match_score"] = max(0, min(100, int(parsed.get("match_score", 0))))
        return parsed
    except Exception as exc:
        print(f"[job_analyzer] LLM analysis failed, using heuristics: {exc}")
        tech_stack = _extract_tech_stack(job_description)
        profile_blob = json.dumps(profile, ensure_ascii=False).lower()
        overlap = sum(1 for kw in tech_stack if kw.lower() in profile_blob)
        score = min(100, 30 + overlap * 10 + (20 if _detect_job_type(job_description) in ("Werkstudent", "Vollzeit") else 0))
        return {
            "tech_stack": tech_stack,
            "key_requirements": [],
            "match_score": score,
            "experience_level": _detect_experience_level(job_description),
            "job_type": _detect_job_type(job_description),
            "location": _detect_location(job_description),
            "summary": f"Heuristic analysis: {overlap} skill overlaps detected.",
        }


def analyze_job_description(
    job_url: str,
    job_description: str,
    profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if profile is None:
        profile = load_profile()
    llm_result = _llm_analyze(job_description, profile)
    if not llm_result.get("tech_stack"):
        llm_result["tech_stack"] = _extract_tech_stack(job_description)
    if not llm_result.get("location"):
        llm_result["location"] = _detect_location(job_description)
    if not llm_result.get("experience_level") or llm_result["experience_level"] == "Unbekannt":
        llm_result["experience_level"] = _detect_experience_level(job_description)
    if not llm_result.get("job_type") or llm_result["job_type"] == "Unbekannt":
        llm_result["job_type"] = _detect_job_type(job_description)
    llm_result["analyzed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    llm_result["job_url"] = job_url
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", job_url)[:80]
    out_path = ANALYSIS_DIR / f"{safe_name}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(llm_result, f, ensure_ascii=False, indent=2)
    return llm_result


def load_analysis(job_url: str) -> Optional[Dict[str, Any]]:
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", job_url)[:80]
    out_path = ANALYSIS_DIR / f"{safe_name}.json"
    if not out_path.exists():
        return None
    with out_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def list_all_analyses() -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    if not ANALYSIS_DIR.exists():
        return results
    for path in sorted(ANALYSIS_DIR.glob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            results.append(data)
        except Exception:
            continue
    return results