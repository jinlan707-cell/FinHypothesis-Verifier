import os
import json
import requests
from dotenv import load_dotenv

# Load .env
load_dotenv(".env")
ifind_token = os.getenv("IFinD_MCP", "").strip().strip('"')
fuyao_key = os.getenv("fuyao_Key", "").strip().strip('"')

print(f"Loaded IFinD Token: {ifind_token[:15]}... (len {len(ifind_token)})")
print(f"Loaded Fuyao Key: {fuyao_key[:10]}... (len {len(fuyao_key)})")

# ==========================================
# 1. TEST FUYAO REST API & MCP
# ==========================================
print("\n" + "="*50)
print("TESTING FUYAO PLATFORM")
print("="*50)

fuyao_base = "https://fuyao.aicubes.cn"
fuyao_headers = {
    "X-Api-Key": fuyao_key,
    "Content-Type": "application/json"
}

# 1.1 Ticker Search
try:
    r = requests.get(f"{fuyao_base}/api/meta/tickers/search?q=贵州茅台", headers=fuyao_headers, timeout=10)
    print(f"[Fuyao] GET /api/meta/tickers/search?q=贵州茅台 -> {r.status_code}")
    print("Response:", json.dumps(r.json(), ensure_ascii=False, indent=2)[:400])
except Exception as e:
    print("[Fuyao] Error ticker search:", e)

# 1.2 Financial Indicators
try:
    r = requests.get(f"{fuyao_base}/api/a-share/financials/indicators?thscode=600519.SH&report=2024-4", headers=fuyao_headers, timeout=10)
    print(f"\n[Fuyao] GET /api/a-share/financials/indicators?thscode=600519.SH&report=2024-4 -> {r.status_code}")
    res_json = r.json()
    print("RateLimit Headers:", {k: v for k, v in r.headers.items() if 'ratelimit' in k.lower()})
    print("Indicators Response Summary:")
    if res_json.get("data") and "abilities" in res_json["data"]:
        for ab in res_json["data"]["abilities"]:
            print(f"  Ability: {ab.get('type')} ({ab.get('name', '')}) -> {len(ab.get('indicators', []))} indicators")
            # print first 2
            for ind in ab.get('indicators', [])[:2]:
                print(f"    - {ind.get('index_id')}: {ind.get('value')} ({ind.get('name', '')})")
    else:
        print(res_json)
except Exception as e:
    print("[Fuyao] Error financial indicators:", e)

# 1.3 Financial Income Statements
try:
    r = requests.get(f"{fuyao_base}/api/a-share/financials/income-statements?thscode=600519.SH&limit=2", headers=fuyao_headers, timeout=10)
    print(f"\n[Fuyao] GET /api/a-share/financials/income-statements?thscode=600519.SH&limit=2 -> {r.status_code}")
    print("Income Statement Response:", json.dumps(r.json(), ensure_ascii=False, indent=2)[:500])
except Exception as e:
    print("[Fuyao] Error income statements:", e)

# 1.4 News / Announcement
try:
    r = requests.get(f"{fuyao_base}/api/news/stock/timeline-list?thscode=600519.SH&offset=first", headers=fuyao_headers, timeout=10)
    print(f"\n[Fuyao] GET /api/news/stock/timeline-list?thscode=600519.SH&offset=first -> {r.status_code}")
    news_json = r.json()
    print("News timeline:", json.dumps(news_json, ensure_ascii=False, indent=2)[:500])
except Exception as e:
    print("[Fuyao] Error news timeline:", e)


# ==========================================
# 2. TEST IFIND MCP
# ==========================================
print("\n" + "="*50)
print("TESTING IFIND MCP")
print("="*50)

ifind_servers = [
    "https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-news-mcp",
    "https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-stock-mcp",
    "https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-edb-mcp",
    "https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-fund-mcp"
]

init_body = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "1.0"}
    }
}

for s_url in ifind_servers:
    print(f"\n--- Testing server: {s_url} ---")
    headers_bearer = {
        "Authorization": f"Bearer {ifind_token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"
    }
    headers_raw = {
        "Authorization": ifind_token,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"
    }
    
    # Try with Bearer
    for h_name, h in [("Bearer", headers_bearer), ("Raw", headers_raw)]:
        try:
            r = requests.post(s_url, headers=h, json=init_body, timeout=8, verify=False)
            print(f"[{h_name}] POST init -> Status {r.status_code}")
            print(f"[{h_name}] Response: {r.text[:300]}")
            if r.status_code == 200:
                # If 200, try tools/list
                tools_body = {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {}
                }
                r_tools = requests.post(s_url, headers=h, json=tools_body, timeout=8, verify=False)
                print(f"[{h_name}] POST tools/list -> Status {r_tools.status_code}")
                print(f"[{h_name}] Tools Response: {r_tools.text[:500]}")
                break
        except Exception as e:
            print(f"[{h_name}] Request error:", e)
