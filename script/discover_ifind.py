import os
import json
import requests
from dotenv import load_dotenv

load_dotenv(".env")
ifind_token = os.getenv("IFinD_MCP", "").strip().strip('"')

potential_servers = [
    "hexin-ifind-ds-news-mcp",
    "hexin-ifind-ds-stock-mcp",
    "hexin-ifind-ds-edb-mcp",
    "hexin-ifind-ds-fund-mcp",
    "hexin-ifind-ds-report-mcp",
    "hexin-ifind-ds-industry-mcp",
    "hexin-ifind-ds-bond-mcp",
    "hexin-ifind-ds-announcement-mcp"
]

base_url = "https://api-mcp.51ifind.com:8643/ds-mcp-servers"

results = {}

for s_name in potential_servers:
    url = f"{base_url}/{s_name}"
    headers = {
        "Authorization": f"Bearer {ifind_token}",
        "Content-Type": "application/json"
    }
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"}
        }
    }
    try:
        r_init = requests.post(url, headers=headers, json=init_payload, timeout=5, verify=False)
        if r_init.status_code == 200:
            # fetch tools
            tools_payload = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {}
            }
            r_tools = requests.post(url, headers=headers, json=tools_payload, timeout=5, verify=False)
            if r_tools.status_code == 200:
                t_data = r_tools.json().get("result", {}).get("tools", [])
                results[s_name] = {
                    "status": "online",
                    "tools": [{
                        "name": t.get("name"),
                        "description": t.get("description"),
                        "parameters": t.get("inputSchema")
                    } for t in t_data]
                }
            else:
                results[s_name] = {"status": "init_ok_tools_fail", "code": r_tools.status_code, "text": r_tools.text}
        else:
            results[s_name] = {"status": "offline", "code": r_init.status_code}
    except Exception as e:
        results[s_name] = {"status": "error", "error": str(e)}

with open("ifind_tools_discovery.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print("Discovery finished. Summary of online servers:")
for k, v in results.items():
    if v.get("status") == "online":
        print(f"Server: {k} -> {len(v['tools'])} tools:")
        for t in v["tools"]:
            print(f"  - {t['name']}: {t['description'][:60]}...")
    else:
        print(f"Server: {k} -> Status: {v.get('status')} ({v.get('code', '')})")
