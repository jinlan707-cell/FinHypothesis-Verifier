import time
import requests
import logging
from typing import List, Dict, Any, Optional
from fin_verifier.core.config import FUYAO_KEY
from fin_verifier.tools.resilience import retry_with_backoff, TransientAPIError, NonRetryableAPIError

logger = logging.getLogger("fin_verifier.tools.fuyao_client")

# 扶摇五维财务指标标准中文名称与默认单位映射表（消除 item.get('name') 产生的 None 错误）
INDEX_ID_CN_MAP: Dict[str, Dict[str, str]] = {
    # 偿债能力 solvency
    "cash_ratio": {"name": "现金比率", "unit": "%"},
    "current_ratio": {"name": "流动比率", "unit": "倍"},
    "quick_ratio": {"name": "速动比率", "unit": "倍"},
    "earned_interest_multiple": {"name": "已获利息倍数", "unit": "倍"},
    "assets_debt_ratio": {"name": "资产负债率", "unit": "%"},
    # 盈利能力 profitability
    "total_assets_net_ratio": {"name": "总资产净利率", "unit": "%"},
    "index_deduct_weighted_avg_roe": {"name": "扣非加权平均净资产收益率", "unit": "%"},
    "sale_gross_margin": {"name": "销售毛利率", "unit": "%"},
    "sale_net_interest_ratio": {"name": "销售净利率", "unit": "%"},
    "index_weighted_avg_roe": {"name": "加权平均净资产收益率", "unit": "%"},
    # 营运能力 operation
    "total_assets_turnover_ratio": {"name": "总资产周转率", "unit": "次"},
    "inventory_turnover_ratio": {"name": "存货周转率", "unit": "次"},
    "long_term_debt_equity_ratio": {"name": "产权比率", "unit": "%"},
    "current_assets_turnover_ratio": {"name": "流动资产周转率", "unit": "次"},
    "receive_account_turnover_ratio": {"name": "应收账款周转率", "unit": "次"},
    # 现金流 cash-flow
    "net_profit_cash_content": {"name": "净利润现金含量", "unit": "%"},
    "cash_operating_index": {"name": "现金营运指数", "unit": "倍"},
    "operating_cash_flow_net_divide_income": {"name": "经营活动净现金流占营收比", "unit": "%"},
    "cash_meet_invest_ratio": {"name": "现金满足投资比率", "unit": "倍"},
    # 成长能力 growth
    "calculate_operating_income_yoy_growth_ratio": {"name": "营业收入同比增长率", "unit": "%"},
    "calculate_operating_profit_yoy_growth_ratio": {"name": "营业利润同比增长率", "unit": "%"},
    "total_assets_growth_ratio": {"name": "总资产增长率", "unit": "%"},
    "calculate_parent_holder_net_profit_yoy_growth_ratio": {"name": "归母净利润同比增长率", "unit": "%"},
}

class FuyaoClient:
    """
    扶摇开放平台 REST API 客户端。
    支持：标的消歧、财报三张表、财务五维指标、估值快照、同花顺行业反查与历史行情。
    """
    BASE_URL = "https://fuyao.aicubes.cn"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or FUYAO_KEY
        if not self.api_key:
            logger.warning("FuyaoClient 初始化但未配置 FUYAO_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json"
        })

    def _check_response(self, resp: requests.Response) -> Dict[str, Any]:
        """统一检查响应状态码与扶摇业务错误码"""
        if resp.status_code in (401, 403):
            raise NonRetryableAPIError(f"扶摇 API 鉴权失败 (HTTP {resp.status_code}): {resp.text}")
        if resp.status_code == 429:
            raise TransientAPIError(f"扶摇 API 触发 HTTP 429 限流: {resp.text}")
        if resp.status_code != 200:
            raise NonRetryableAPIError(f"扶摇 API 返回非预期状态码 (HTTP {resp.status_code}): {resp.text}")

        try:
            data = resp.json()
        except Exception as e:
            raise NonRetryableAPIError(f"扶摇 API 返回无法解析为 JSON: {e}")

        code = data.get("code")
        if code == 4001:
            raise TransientAPIError(f"扶摇 API 触发平台限流 (code=4001): {data.get('message')}")
        if code != 0 and code is not None:
            raise NonRetryableAPIError(f"扶摇 API 业务报错 (code={code}): {data.get('message')}")

        return data

    @retry_with_backoff(max_attempts=3, initial_delay=1.0)
    def search_ticker(self, keyword: str) -> List[Dict[str, Any]]:
        """标的消歧检索，返回匹配候选列表"""
        url = f"{self.BASE_URL}/api/meta/tickers/search"
        params = {"q": keyword.strip()}
        resp = self.session.get(url, params=params, timeout=10)
        res_json = self._check_response(resp)
        data = res_json.get("data", [])
        if isinstance(data, dict) and "item" in data:
            return data["item"]
        if isinstance(data, list):
            return data
        return []

    @retry_with_backoff(max_attempts=3, initial_delay=1.0)
    def get_income_statements(self, thscode: str, period: str = "quarterly", limit: int = 8) -> List[Dict[str, Any]]:
        """获取利润表（原币元，年初至今累计值 YTD）"""
        url = f"{self.BASE_URL}/api/a-share/financials/income-statements"
        params = {"thscode": thscode, "period": period, "limit": limit}
        resp = self.session.get(url, params=params, timeout=10)
        res_json = self._check_response(resp)
        data = res_json.get("data", [])
        if isinstance(data, dict) and "item" in data:
            return data["item"]
        if isinstance(data, list):
            return data
        return []

    @retry_with_backoff(max_attempts=3, initial_delay=1.0)
    def get_balance_sheets(self, thscode: str, period: str = "quarterly", limit: int = 8) -> List[Dict[str, Any]]:
        """获取资产负债表（原币元，时点余额）"""
        url = f"{self.BASE_URL}/api/a-share/financials/balance-sheets"
        params = {"thscode": thscode, "period": period, "limit": limit}
        resp = self.session.get(url, params=params, timeout=10)
        res_json = self._check_response(resp)
        data = res_json.get("data", [])
        if isinstance(data, dict) and "item" in data:
            return data["item"]
        if isinstance(data, list):
            return data
        return []

    @retry_with_backoff(max_attempts=3, initial_delay=1.0)
    def get_cash_flow_statements(self, thscode: str, period: str = "quarterly", limit: int = 8) -> List[Dict[str, Any]]:
        """获取现金流量表（原币元，累计值）"""
        url = f"{self.BASE_URL}/api/a-share/financials/cash-flow-statements"
        params = {"thscode": thscode, "period": period, "limit": limit}
        resp = self.session.get(url, params=params, timeout=10)
        res_json = self._check_response(resp)
        data = res_json.get("data", [])
        if isinstance(data, dict) and "item" in data:
            return data["item"]
        if isinstance(data, list):
            return data
        return []

    @retry_with_backoff(max_attempts=3, initial_delay=1.0)
    def get_financial_indicators(self, thscode: str, report: str) -> Dict[str, Any]:
        """
        获取五维财务指标，report 格式如 '2023-4' 或 '2024-3'
        """
        url = f"{self.BASE_URL}/api/a-share/financials/indicators"
        params = {"thscode": thscode, "report": report}
        resp = self.session.get(url, params=params, timeout=10)
        res_json = self._check_response(resp)
        return res_json.get("data", {})

    @retry_with_backoff(max_attempts=3, initial_delay=1.0)
    def get_valuation_snapshot(self, thscode: str) -> Dict[str, Any]:
        """获取最新估值快照（pe_ttm, pe_mrq, pb_mrq, ps_ttm, pcf_ttm）"""
        url = f"{self.BASE_URL}/api/a-share/valuations/snapshot"
        params = {"thscodes": thscode}
        resp = self.session.get(url, params=params, timeout=10)
        res_json = self._check_response(resp)
        data = res_json.get("data", {})
        if isinstance(data, dict):
            items = data.get("item", [])
            if items and isinstance(items, list):
                return items[0]
        elif isinstance(data, list) and data:
            return data[0]
        return {}

    @retry_with_backoff(max_attempts=3, initial_delay=1.0)
    def get_stock_historical_prices(
        self,
        thscode: str,
        start_date: str,
        end_date: str,
        adjust: str = "forward"
    ) -> List[Dict[str, Any]]:
        """
        获取个股历史前复权 K 线序列
        start_date, end_date 格式为 YYYY-MM-DD
        """
        s_ms = int(time.mktime(time.strptime(start_date, "%Y-%m-%d")) * 1000)
        e_ms = int(time.mktime(time.strptime(end_date, "%Y-%m-%d")) * 1000)
        url = f"{self.BASE_URL}/api/a-share/prices/historical"
        params = {
            "thscode": thscode,
            "interval": "1d",
            "start": s_ms,
            "end": e_ms,
            "adjust": adjust
        }
        resp = self.session.get(url, params=params, timeout=10)
        res_json = self._check_response(resp)
        data = res_json.get("data", {})
        if isinstance(data, dict):
            return data.get("item", [])
        return []

    @retry_with_backoff(max_attempts=3, initial_delay=1.0)
    def get_stock_industry(self, thscode: str) -> Optional[Dict[str, str]]:
        """反查个股所属同花顺行业指数（如 881273.TI 白酒）"""
        url = f"{self.BASE_URL}/api/a-share-index/constituents/ths-index-list-by-stock"
        params = {"thscode": thscode, "type": "industry"}
        resp = self.session.get(url, params=params, timeout=10)
        res_json = self._check_response(resp)
        items = res_json.get("data", {}).get("item", [])
        if items:
            idx_list = items[0].get("index_list", [])
            if idx_list:
                return {
                    "thscode": idx_list[0].get("thscode"),
                    "name": idx_list[0].get("name")
                }
        return None

    @retry_with_backoff(max_attempts=3, initial_delay=1.0)
    def get_index_historical_prices(self, thscode: str, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """
        获取行业指数历史 K 线序列
        start_date, end_date 格式为 YYYY-MM-DD
        """
        s_ms = int(time.mktime(time.strptime(start_date, "%Y-%m-%d")) * 1000)
        e_ms = int(time.mktime(time.strptime(end_date, "%Y-%m-%d")) * 1000)
        url = f"{self.BASE_URL}/api/a-share-index/prices/historical"
        params = {
            "thscode": thscode,
            "interval": "1d",
            "start": s_ms,
            "end": e_ms
        }
        resp = self.session.get(url, params=params, timeout=10)
        res_json = self._check_response(resp)
        return res_json.get("data", {}).get("item", [])

    @staticmethod
    def disaggregate_quarterly_flow(
        statements: List[Dict[str, Any]],
        metric_key: str
    ) -> Dict[str, float]:
        """
        将年初至今累计 (YTD) 流量拆解为单季度数值。
        规则：
        1. Q1 单季 = Q1 累计值；
        2. 同年相邻累计期相减：Q2单季 = Q2累计 - Q1累计；Q3单季 = Q3累计 - Q2累计；Q4单季 = FY累计 - Q3累计；
        3. 累计数据缺失时，不虚假推断。
        返回格式: {"2023-Q1": float, "2023-Q2": float, ...}（金额已统一转为亿元）
        """
        parsed_by_year_quarter = {}
        for st in statements:
            val = st.get(metric_key)
            if val is None:
                continue

            # 优先使用真实扶摇物理接口字段 fiscal_year (int/str) 与 fiscal_period ('Q1'~'Q4', 'FY')
            fy = st.get("fiscal_year")
            fp = st.get("fiscal_period")
            year = None
            q = None

            if fy is not None and fp is not None:
                year = str(fy).strip()
                fp_upper = str(fp).strip().upper()
                if fp_upper in ("Q4", "FY"):
                    q = "FY"
                elif fp_upper in ("Q1", "Q2", "Q3"):
                    q = fp_upper
            else:
                # 兼容遗留测试数据或备用字段 report_date / end_date
                date_str = str(st.get("report_date") or st.get("end_date") or "").strip()
                if date_str:
                    year = date_str[:4]
                    month = date_str[5:7] if len(date_str) >= 7 else ""
                    if month in ("03", "1", "01"):
                        q = "Q1"
                    elif month in ("06", "6", "04"):
                        q = "Q2"
                    elif month in ("09", "9", "07"):
                        q = "Q3"
                    elif month in ("12", "10"):
                        q = "FY"

            if not year or not q:
                continue

            # 换算为亿元浮点数
            val_yi = float(val) / 100000000.0
            if year not in parsed_by_year_quarter:
                parsed_by_year_quarter[year] = {}
            parsed_by_year_quarter[year][q] = val_yi

        single_quarter_results = {}
        for year, q_map in parsed_by_year_quarter.items():
            if "Q1" in q_map:
                single_quarter_results[f"{year}-Q1"] = round(q_map["Q1"], 4)
            if "Q2" in q_map and "Q1" in q_map:
                single_quarter_results[f"{year}-Q2"] = round(q_map["Q2"] - q_map["Q1"], 4)
            if "Q3" in q_map and "Q2" in q_map:
                single_quarter_results[f"{year}-Q3"] = round(q_map["Q3"] - q_map["Q2"], 4)
            if "FY" in q_map and "Q3" in q_map:
                single_quarter_results[f"{year}-Q4"] = round(q_map["FY"] - q_map["Q3"], 4)

        return single_quarter_results
