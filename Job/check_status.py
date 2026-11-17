import json
from collections import Counter
with open('history.json','r',encoding='utf-8-sig') as f:
    h = json.load(f)
c = Counter(e.get('application_status','?') for e in h.values())
print('Status counts:', dict(c))
print('Has status_source manual_reset:', any(e.get('status_source')=='manual_reset' for e in h.values()))
# Check first entry
first = list(h.items())[0]
print('First entry status:', first[1].get('application_status'))
