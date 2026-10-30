import os
import json
import time
import re
import requests
import urllib3
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv(".env")

fuyao_key = os.getenv("fuyao_Key", "").strip().strip('"')
ifind_token = os.getenv("IFinD_MCP", "").strip().strip('"')
sf_key = os.getenv("API_Key", "").strip().strip('"')
sf_url = os.getenv("Base_url", "https://api.siliconflow.cn/v1").strip().strip('"')
sf_model = os.getenv("Model", "deepseek-ai/DeepSeek-V4.1-Flash").strip().strip('"')

results = {}

# ==============================================================================
# 1. 验证扶摇个股反查同花顺行业指数与历史K线
# ==============================================================================
print("=== 1. VERIFY FUYAO INDUSTRY BENCHMARK ===")
headers_fuyao = {"X-api-key": fuyao_key}
url_ind = "https://fuyao.aicubes.cn/api/a-share-index/constituents/ths-index-list-by-stock"
params_ind = {"thscode": "600519.SH", "type": "industry"}

try:
    r_ind = requests.get(url_ind, headers=headers_fuyao, params=params_ind, timeout=10)
    ind_res = r_ind.json()
    print("Industry query status:", r_ind.status_code, "code:", ind_res.get("code"))
    
    ths_code = None
    ths_name = None
    if ind_res.get("data", {}).get("item"):
        idx_list = ind_res["data"]["item"][0].get("index_list", [])
        if idx_list:
            ths_code = idx_list[0].get("thscode")
            ths_name = idx_list[0].get("name")
            print(f"Matched: {ths_name} ({ths_code})")
    
    kline_ok = False
    kline_ret = None
    if ths_code:
        # 2023-01-01 to 2024-01-01 ms
        s_ms = int(time.mktime(time.strptime("2023-01-01", "%Y-%m-%d")) * 1000)
        e_ms = int(time.mktime(time.strptime("2024-01-01", "%Y-%m-%d")) * 1000)
        url_k = "https://fuyao.aicubes.cn/api/a-share-index/prices/historical"
        params_k = {"thscode": ths_code, "interval": "1d", "start": s_ms, "end": e_ms}
        r_k = requests.get(url_k, headers=headers_fuyao, params=params_k, timeout=10)
        k_res = r_k.json()
        print("Historical K status:", r_k.status_code, "code:", k_res.get("code"))
        k_items = k_res.get("data", {}).get("item", [])
        print(f"Historical K bars count: {len(k_items)}")
        if k_items:
            open_p = k_items[0]["open_price"]
            close_p = k_items[-1]["close_price"]
            kline_ret = round(((close_p - open_p) / open_p) * 100, 2)
            kline_ok = True
            print(f"Industry Return 2023: {open_p} -> {close_p} ({kline_ret}%)")

    results["fuyao_industry"] = {
        "status": "PASS" if kline_ok else "FAIL",
        "stock": "600519.SH",
        "industry_name": ths_name,
        "industry_code": ths_code,
        "bars_count": len(k_items) if kline_ok else 0,
        "return_pct": kline_ret
    }
except Exception as e:
    print("Fuyao industry error:", e)
    results["fuyao_industry"] = {"status": "ERROR", "error": str(e)}


# ==============================================================================
# 2. 验证 iFinD get_stock_financials 表格格式与单位归一化
# ==============================================================================
print("\n=== 2. VERIFY IFIND FINANCIALS & UNIT PARSER ===")
url_ifind = "https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-stock-mcp"
payload_ifind = {
    "jsonrpc": "2.0",
    "id": 201,
    "method": "tools/call",
    "params": {
        "name": "get_stock_financials",
        "arguments": {
            "query": "贵州茅台 2024年三季报 扣除非经常性损益 净利润 存货 市盈率PE"
        }
    }
}
headers_ifind = {
    "Authorization": f"Bearer {ifind_token}",
    "Content-Type": "application/json"
}

try:
    r_fin = requests.post(url_ifind, headers=headers_ifind, json=payload_ifind, timeout=15, verify=False)
    print("iFinD get_stock_financials status:", r_fin.status_code)
    raw_text = r_fin.json().get("result", {}).get("content", [{}])[0].get("text", "")
    inner = json.loads(raw_text)
    answer = inner.get("data", {}).get("answer", "")
    print("iFinD answer markdown table preview:\n", answer[:400])

    # Test unit normalizer logic
    def normalize_unit(val_str):
        if not val_str or val_str.strip() in ("--", "-", "null", "None"):
            return None, "null"
        val_str = str(val_str).replace(",", "").strip()
        if "亿" in val_str:
            num = float(re.findall(r"[-+]?\d*\.?\d+", val_str)[0])
            return round(num, 4), "亿元"
        elif "万" in val_str:
            num = float(re.findall(r"[-+]?\d*\.?\d+", val_str)[0])
            return round(num / 10000.0, 4), "亿元"
        elif "%" in val_str:
            num = float(re.findall(r"[-+]?\d*\.?\d+", val_str)[0])
            return round(num, 4), "%"
        else:
            try:
                num = float(re.findall(r"[-+]?\d*\.?\d+", val_str)[0])
                if abs(num) >= 1e8:
                    return round(num / 1e8, 4), "亿元"
                return round(num, 4), "元"
            except:
                return None, "unknown"

    # parse markdown table
    rows = [r.strip() for r in answer.strip().split("\n") if r.strip()]
    parsed_cells = {}
    if len(rows) >= 3:
        headers = [c.strip() for c in rows[0].split("|") if c.strip()]
        values = [c.strip() for c in rows[2].split("|") if c.strip()]
        for h, v in zip(headers, values):
            norm_val, norm_unit = normalize_unit(v)
            parsed_cells[h] = {"raw": v, "norm_val": norm_val, "norm_unit": norm_unit}
            print(f"  Column [{h}]: raw '{v}' -> normalized {norm_val} {norm_unit}")

    results["ifind_financials"] = {
        "status": "PASS" if parsed_cells else "FAIL",
        "columns_parsed": len(parsed_cells),
        "sample": parsed_cells
    }
except Exception as e:
    print("iFinD financials error:", e)
    results["ifind_financials"] = {"status": "ERROR", "error": str(e)}


# ==============================================================================
# 3. 验证 SiliconFlow DeepSeek V4.1 Flash 结构化 JSON 输出
# ==============================================================================
print("\n=== 3. VERIFY SILICONFLOW LLM JSON MODE ===")
from openai import OpenAI

try:
    client = OpenAI(base_url=sf_url, api_key=sf_key)
    t0 = time.time()
    resp = client.chat.completions.create(
        model=sf_model,
        messages=[
            {
                "role": "system",
                "content": "你是一个金融事实解析助手。请根据用户输入的命题，输出且仅输出一个合法的 JSON 对象，不要包含 markdown 代码块。JSON 结构必须包含: ticker (string), hypothesis_clarified (string), sub_hypotheses (array of {id: string, statement: string, test_dimension: string})."
            },
            {
                "role": "user",
                "content": "分析标的: 600519.SH 贵州茅台。命题: 茅台近年利润稳健增长主要依靠产品提价与直销放量，而非单纯产能扩张。"
            }
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=600
    )
    duration_ms = int((time.time() - t0) * 1000)
    llm_content = resp.choices[0].message.content.strip()
    print(f"LLM Response Time: {duration_ms}ms")
    print("LLM Content Preview:\n", llm_content[:300])

    # test JSON loads
    # handle possible ```json fence if any
    clean_json = llm_content
    if clean_json.startswith("```"):
        clean_json = re.sub(r"^```[a-zA-Z]*\n?", "", clean_json)
        clean_json = re.sub(r"\n?```$", "", clean_json)
    
    parsed_json = json.loads(clean_json)
    print("JSON Parsed Successfully! Keys:", list(parsed_json.keys()))
    print("Sub-hypotheses count:", len(parsed_json.get("sub_hypotheses", [])))
    for sh in parsed_json.get("sub_hypotheses", []):
        print(f"  - {sh.get('id')}: {sh.get('statement')}")

    results["siliconflow_llm"] = {
        "status": "PASS",
        "duration_ms": duration_ms,
        "model": sf_model,
        "parsed_keys": list(parsed_json.keys()),
        "sub_hypotheses_count": len(parsed_json.get("sub_hypotheses", []))
    }
except Exception as e:
    print("SiliconFlow error:", e)
    results["siliconflow_llm"] = {"status": "ERROR", "error": str(e)}

with open("verification_conclusions.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print("\n=== ALL VERIFICATIONS COMPLETED! Summary saved to verification_conclusions.json ===")
