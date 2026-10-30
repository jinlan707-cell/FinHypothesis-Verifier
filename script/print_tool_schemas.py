import json

with open('ifind_tools_discovery.json', 'r', encoding='utf-8') as f:
    disc = json.load(f)

for server, s_val in disc.items():
    if s_val.get('status') == 'online':
        print(f"\n{'='*20} {server} {'='*20}")
        for t in s_val['tools']:
            print(f"\nTool: {t['name']}")
            print(f"Description: {t['description']}")
            print(f"Parameters: {json.dumps(t['parameters'], ensure_ascii=False, indent=2)}")
