import json
from pathlib import Path

print("=" * 60)
print("JOB APPLIER - FINAL VERIFICATION")
print("=" * 60)

h_path = Path("history.json")
if h_path.exists():
    h = json.load(open(h_path, encoding="utf-8"))
    print(f"history.json: {len(h)} records")
else:
    print("history.json: NOT FOUND")

t_path = Path("targets.json")
if t_path.exists():
    targets = json.load(open(t_path, encoding="utf-8"))
    print(f"\ntargets.json: {len(targets)} entries")
    for t in targets[:5]:
        print(f"  {t.get('company', 'Unknown')} | {t.get('job_title', '?')} | {t.get('location', '?')} | tier={t.get('tier', '?')} | score={t.get('match_score', 0)} | source={t.get('source', '?')}")
else:
    print("\ntargets.json: NOT FOUND")

apps_dir = Path("_applications")
if apps_dir.exists():
    apps = [d for d in apps_dir.iterdir() if d.is_dir()]
    print(f"\n_applications/: {len(apps)} folders")
    for d in sorted(apps):
        files = [f.name for f in d.iterdir() if f.is_file()]
        print(f"  {d.name}: {files}")
else:
    print("\n_applications/: NOT FOUND")
