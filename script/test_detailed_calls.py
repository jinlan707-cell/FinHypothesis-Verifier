import os
import json
import requests
from dotenv import load_dotenv

load_dotenv(".env")
ifind_token = os.getenv("IFinD_MCP", "").strip().strip('"')
fuyao_key = os.getenv("fuyao_Key", "").strip().strip('"')

headers_fuyao = {
    "X-Api-Key": fuyao_key,
    "Content-Type": "application/json"
}

headers_ifind = {
    "Authorization": f"Bearer {ifind_token}",
    "Content-Type": "application/json"
}

def call_ifind_tool(server, tool_name, arguments):
    url = f"https://api-mcp.51ifind.com:8643/ds-mcp-servers/{server}"
    payload = {
        "jsonrpc": "2.0",
        "id": 100,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        }
    }
    r = requests.post(url, headers=headers_ifind, json=payload, timeout=15, verify=False)
    return r.status_code, r.json()

print("="*60)
print("TEST 1: iFinD search_notice (公告)")
print("="*60)
status, res = call_ifind_tool(
    "hexin-ifind-ds-news-mcp",
    "search_notice",
    {
        "query": "贵州茅台 2024年年度报告 或 第三季度报告",
        "size": 2,
        "time_start": "2024-01-01",
        "time_end": "2025-01-01"
    }
)
print("search_notice status:", status)
with open("test_ifind_notice.json", "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2)
print("Saved to test_ifind_notice.json")

print("\n" + "="*60)
print("TEST 2: iFinD get_stock_financials (财务指标)")
print("="*60)
status, res = call_ifind_tool(
    "hexin-ifind-ds-stock-mcp",
    "get_stock_financials",
    {
        "query": "贵州茅台 2024年三季报 营业收入 归母净利润 毛利率 ROE"
    }
)
print("get_stock_financials status:", status)
with open("test_ifind_financials.json", "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2)
print("Saved to test_ifind_financials.json")

print("\n" + "="*60)
print("TEST 3: Fuyao Financials Indicators & Statements")
print("="*60)
r_ind = requests.get("https://fuyao.aicubes.cn/api/a-share/financials/indicators?thscode=600519.SH&report=2024-3", headers=headers_fuyao, timeout=10)
r_inc = requests.get("https://fuyao.aicubes.cn/api/a-share/financials/income-statements?thscode=600519.SH&period=quarterly&limit=2", headers=headers_fuyao, timeout=10)
r_val = requests.get("https://fuyao.aicubes.cn/api/a-share/valuations/snapshot?thscode=600519.SH", headers=headers_fuyao, timeout=10)

fuyao_test_data = {
    "indicators_status": r_ind.status_code,
    "indicators_headers": dict(r_ind.headers),
    "indicators_data": r_ind.json(),
    "income_statement_status": r_inc.status_code,
    "income_statement_data": r_inc.json(),
    "valuation_status": r_val.status_code,
    "valuation_data": r_val.json()
}
with open("test_fuyao_data.json", "w", encoding="utf-8") as f:
    json.dump(fuyao_test_data, f, ensure_ascii=False, indent=2)
print("Saved to test_fuyao_data.json")
