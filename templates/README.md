# /templates — Unified Job Applier + browser-use automation

## Files
- `job_applier_browser_template.py` — reusable orchestrator tying the Job Applier bot (`Job/`) into the browser-use agent runner (`web-ui/`).

## Hook modes (`JOB_APPLIER_HOOK_MODE` / `--hook-mode`)
| Mode | What it does |
|------|--------------|
| `discover` | Browser agent surfs job boards, extracts job URLs, writes `Job/targets.json` |
| `apply` | Runs the Job Applier batch processor over `Job/targets.json` (CV + cover letter per target) |
| `full` | `discover` → build documents → apply (default) |

## Prerequisites (`web-ui/.env`)
```
GOOGLE_API_KEY=AIza...        # your legacy Google key (replace the placeholder)
DEFAULT_LLM_MODEL=gemini-2.5-flash
```

## Quick start
```powershell
# 1. Launch the Web UI (kills stale port-7788 processes, aligns packages, starts Gradio)
web-ui\Launch-WebUI.bat

# 2. Run the unified template (full pipeline)
python templates\job_applier_browser_template.py --hook-mode full

# 3. Or run only the Job Applier over existing targets
python templates\job_applier_browser_template.py --hook-mode apply --max-jobs 10
```

## Customization
- Wire your real discover/apply task into `run_browser_agent()` in the template.
- Tune batch limits in `Job/batch_processor.py` (hardcoded `MAX_JOBS = 15`, `PAUSE_SECONDS = 20`).
- Choose the CV variant routed per job in `Job/batch_processor.py` (`STATIC_CV_CANDIDATES` / `detect_job_type`).