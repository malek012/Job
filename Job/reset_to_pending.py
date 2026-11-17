import json
from pathlib import Path
from datetime import datetime
import shutil

HISTORY_PATH = Path('history.json')
BACKUP_DIR = Path('_backup_cleanup')

# Create backup
BACKUP_DIR.mkdir(exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
shutil.copy2(HISTORY_PATH, BACKUP_DIR / f"history.{stamp}.json")

# Load
with open(HISTORY_PATH, 'r', encoding='utf-8-sig') as f:
    history = json.load(f)

PROTECTED_STATUSES = {"submitted", "interview", "offer", "accepted", "refused", "withdrawn", "skipped"}

# Count current statuses
from collections import Counter
status_counts = Counter(e.get("application_status", "?") for e in history.values())
print("Current status counts:")
for status, count in sorted(status_counts.items()):
    print(f"  {status}: {count}")

# Reset needs_manual entries to pending
to_reset = []
for url, entry in history.items():
    current = entry.get("application_status", "")
    if current == "needs_manual":
        to_reset.append(url)

print(f"\nFound {len(to_reset)} needs_manual entries to reset to pending")

now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
for url in to_reset:
    entry = history[url]
    entry["application_status"] = "pending"
    entry["application_status_label"] = "Pending / Waiting"
    entry["status_updated_at"] = now
    entry["status_source"] = "manual_reset"
    entry.pop("submitted_at", None)
    entry.pop("submission_note", None)
    entry.pop("automation_blocker", None)
    history[url] = entry

# Write directly
with open(HISTORY_PATH, 'w', encoding='utf-8') as f:
    json.dump(history, f, ensure_ascii=False, indent=2)
    f.flush()

print(f"\nReset {len(to_reset)} entries from needs_manual -> pending")

# Verify
new_counts = Counter(e.get("application_status", "?") for e in history.values())
print("\nNew status counts:")
for status, count in sorted(new_counts.items()):
    print(f"  {status}: {count}")
