import os
import json
import requests
from dotenv import load_dotenv

load_dotenv(".env")
ifind_token = os.getenv("IFinD_MCP", "").strip().strip('"')
fuyao_key = os.getenv("fuyao_Key", "").strip().strip('"')

# 1. Test Fuyao Valuation snapshot with correct parameter 'thscodes'
r_val = requests.get(
    "https://fuyao.aicubes.cn/api/a-share/valuations/snapshot?thscodes=600519.SH",
    headers={"X-Api-Key": fuyao_key},
    timeout=10
)
print("Fuyao Valuation Snapshot:")
print(json.dumps(r_val.json(), ensure_ascii=False, indent=2))

# 2. Test Fuyao MCP endpoint (a-share)
mcp_fuyao_url = "https://fuyao.aicubes.cn/mcp/a-share"
mcp_init = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "1.0"}
    }
}
r_fuyao_mcp = requests.post(
    mcp_fuyao_url,
    headers={"X-Api-Key": fuyao_key, "Content-Type": "application/json"},
    json=mcp_init,
    timeout=10
)
print("\nFuyao MCP init status:", r_fuyao_mcp.status_code)
print("Fuyao MCP init body:", r_fuyao_mcp.text[:300])

# 3. Test iFinD search_notice with detail inspection
url_notice = "https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-news-mcp"
payload_notice = {
    "jsonrpc": "2.0",
    "id": 102,
    "method": "tools/call",
    "params": {
        "name": "search_notice",
        "arguments": {
            "query": "贵州茅台 2024年半年度报告 核心经营数据 主营业务收入",
            "time_start": "2024-06-01",
            "time_end": "2024-09-01",
            "size": 1
        }
    }
}
r_notice = requests.post(
    url_notice,
    headers={"Authorization": f"Bearer {ifind_token}", "Content-Type": "application/json"},
    json=payload_notice,
    timeout=15,
    verify=False
)
print("\niFinD Notice Call Status:", r_notice.status_code)
notice_json = r_notice.json()
print("iFinD Notice Response Text:")
res_text = notice_json.get("result", {}).get("content", [{}])[0].get("text", "")
try:
    inner_json = json.loads(res_text)
    print("Inner JSON keys:", inner_json.keys())
    data_list = json.loads(inner_json.get("data", "[]"))
    print(f"Data list count: {len(data_list)}")
    if data_list:
        print("First item keys:", data_list[0].keys())
        print("Title:", data_list[0].get("公告标题"))
        print("Date:", data_list[0].get("日期"))
        print("Content preview (first 400 chars):", data_list[0].get("公告片段内容", "")[:400])
        print("Content total length:", len(data_list[0].get("公告片段内容", "")))
except Exception as e:
    print("Parsing error:", e)
    print("Raw text:", res_text[:500])
