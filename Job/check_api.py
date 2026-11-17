import requests
import json

try:
    r = requests.get('http://127.0.0.1:7788/api/state', timeout=5)
    print(f'Status: {r.status_code}')
    data = r.json()
    print(f'Keys: {list(data.keys())}')
    print(f'Metrics: {data.get("metrics", {})}')
    print(f'Targets count: {len(data.get("targets", []))}')
    print(f'History count: {len(data.get("history", {}))}')
    print(f'Review queue: {len(data.get("review_queue", []))}')
    print(f'Log lines: {len(data.get("log", []))}')
    
    if data.get('targets'):
        print(f'\nSample target: {data["targets"][0]}')
    
    if data.get('history'):
        for k, v in list(data['history'].items())[:1]:
            print(f'Sample history entry: {v}')
except Exception as e:
    print(f'Error: {e}')
