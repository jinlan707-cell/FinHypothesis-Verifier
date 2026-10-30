import os
import json
import requests
from dotenv import load_dotenv

load_dotenv(".env")
ifind_token = os.getenv("IFinD_MCP", "").strip().strip('"')

url = "https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-news-mcp"
payload = {
    "jsonrpc": "2.0",
    "id": 103,
    "method": "tools/call",
    "params": {
        "name": "search_news",
        "arguments": {
            "query": "贵州茅台 提价 飞天 业绩说明会",
            "time_start": "2024-01-01",
            "time_end": "2024-12-31",
            "size": 2
        }
    }
}
r = requests.post(url, headers={"Authorization": f"Bearer {ifind_token}", "Content-Type": "application/json"}, json=payload, timeout=15, verify=False)
print("search_news status:", r.status_code)
content = r.json().get("result", {}).get("content", [{}])[0].get("text", "")
try:
    inner = json.loads(content)
    print("search_news inner keys:", inner.keys())
    items = json.loads(inner.get("data", "[]"))
    print(f"Items count: {len(items)}")
    for item in items:
        print("News item:", item)
except Exception as e:
    print("Error:", e, content[:300])
