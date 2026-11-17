import json
from collections import Counter

h = json.load(open('history.json','r',encoding='utf-8-sig'))
c = Counter(e.get('application_status','?') for e in h.values())
print(f'Total entries: {len(h)}')
print()
for k,v in sorted(c.items()):
    print(f'  {k}: {v}')
print()
print('=== PENDING entries (eligible for approval in Review Queue) ===')
pending_count = 0
for url, e in h.items():
    if e.get('application_status') == 'pending':
        pending_count += 1
        docs = e.get('docs_ready', False)
        tier = e.get('tier_label', '?')
        print(f'  [{tier}] {e.get("company","?")} | {e.get("job_title","?")[:60]} | docs_ready={docs}')
print(f'\nTotal pending: {pending_count}')
print()
print('=== SKIPPED entries (protected, excluded by relevance filter) ===')
skipped_count = 0
for url, e in h.items():
    if e.get('application_status') == 'skipped':
        skipped_count += 1
        print(f'  [{e.get("tier_label","?")}] {e.get("company","?")} | {e.get("job_title","?")[:60]} | {e.get("status_note","")}')
print(f'\nTotal skipped: {skipped_count}')
