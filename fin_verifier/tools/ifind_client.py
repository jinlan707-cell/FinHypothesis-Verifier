import json
import logging
import urllib3
from datetime import datetime
from typing import List, Dict, Any, Optional
import requests

from fin_verifier.core.config import IFIND_MCP_TOKEN
from fin_verifier.tools.resilience import retry_with_backoff, TransientAPIError, NonRetryableAPIError
from fin_verifier.tools.unit_normalizer import UnitNormalizer

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger("fin_verifier.tools.ifind_client")

class IFinDClient:
    """
    iFinD MCP JSON-RPC 2.0 客户端。
    支持：
    1. 法定公告语义检索 (search_notice)
    2. 财经新闻与业绩说明会片段检索 (search_news)
    3. 财务与估值表格数据补充 (get_stock_financials)
    """
    NEWS_MCP_URL = "https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-news-mcp"
    STOCK_MCP_URL = "https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-stock-mcp"

    def __init__(self, token: Optional[str] = None):
        self.token = token or IFIND_MCP_TOKEN
        if not self.token:
            logger.warning("IFinDClient 初始化但未配置 IFIND_MCP_TOKEN")
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

    def _call_mcp_tool(self, server_url: str, tool_name: str, arguments: Dict[str, Any], timeout: int = 20) -> Any:
        """底层 JSON-RPC 2.0 工具调用封装"""
        payload = {
            "jsonrpc": "2.0",
            "id": int(datetime.now().timestamp() * 1000),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }

        try:
            resp = requests.post(
                server_url,
                headers=self.headers,
                json=payload,
                timeout=timeout,
                verify=False
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            raise TransientAPIError(f"iFinD MCP 网络连接或超时异常: {e}")
        except Exception as e:
            raise NonRetryableAPIError(f"iFinD MCP 请求构造异常: {e}")

        if resp.status_code in (401, 403):
            raise NonRetryableAPIError(f"iFinD MCP 鉴权失败 (HTTP {resp.status_code}): {resp.text}")
        if resp.status_code == 429:
            raise TransientAPIError(f"iFinD MCP 触发限流 (HTTP 429): {resp.text}")
        if resp.status_code != 200:
            raise NonRetryableAPIError(f"iFinD MCP 返回异常 HTTP 状态码 ({resp.status_code}): {resp.text}")

        try:
            res_json = resp.json()
        except Exception as e:
            raise NonRetryableAPIError(f"iFinD MCP 响应无法解析为 JSON: {e}")

        if "error" in res_json:
            err = res_json["error"]
            raise NonRetryableAPIError(f"iFinD MCP 业务错误: {err}")

        # 提取 content
        content_list = res_json.get("result", {}).get("content", [])
        if not content_list:
            return None

        first_text = content_list[0].get("text", "")
        try:
            inner_json = json.loads(first_text)
            return inner_json
        except Exception:
            return first_text

    @retry_with_backoff(max_attempts=3, initial_delay=1.5)
    def search_notice(
        self,
        query: str,
        time_start: str,
        time_end: str,
        size: int = 5,
        is_replenish: bool = False
    ) -> List[Dict[str, Any]]:
        """
        上市公司法定公告语义检索。
        时间窗口由编排层统一设定（补检回溯前溯 12 个月在编排层计算，客户端内部严禁二次前溯导致双重回溯）。
        """
        args = {
            "query": query.strip(),
            "time_start": time_start,
            "time_end": time_end,
            "size": min(size, 20)
        }

        res = self._call_mcp_tool(self.NEWS_MCP_URL, "search_notice", args, timeout=25)
        if not res:
            return []

        # 嵌套格式解析
        data_str = res.get("data") if isinstance(res, dict) else None
        if not data_str:
            return []

        if isinstance(data_str, list):
            items = data_str
        elif isinstance(data_str, str):
            try:
                items = json.loads(data_str)
            except Exception:
                return []
        else:
            return []

        parsed_items = []
        for it in items:
            if not isinstance(it, dict):
                continue
            # 排除纯备注项
            if "备注" in it and "公告片段内容" not in it:
                continue
            parsed_items.append({
                "title": it.get("公告标题", ""),
                "content": it.get("公告片段内容", ""),
                "date": it.get("日期", "")
            })

        return parsed_items

    @retry_with_backoff(max_attempts=3, initial_delay=1.5)
    def search_news(
        self,
        query: str,
        time_start: str,
        time_end: str,
        size: int = 5
    ) -> List[Dict[str, Any]]:
        """财经新闻资讯与业绩说明会片段检索"""
        args = {
            "query": query.strip(),
            "time_start": time_start,
            "time_end": time_end,
            "size": min(size, 20)
        }
        res = self._call_mcp_tool(self.NEWS_MCP_URL, "search_news", args, timeout=25)
        if not res:
            return []

        data_str = res.get("data") if isinstance(res, dict) else None
        if not data_str:
            return []

        if isinstance(data_str, list):
            items = data_str
        elif isinstance(data_str, str):
            try:
                items = json.loads(data_str)
            except Exception:
                return []
        else:
            return []

        parsed_items = []
        for it in items:
            if not isinstance(it, dict):
                continue
            if "备注" in it and "资讯片段内容" not in it:
                continue
            parsed_items.append({
                "title": it.get("资讯标题", ""),
                "content": it.get("资讯片段内容", ""),
                "date": it.get("日期", "")
            })

        return parsed_items

    @retry_with_backoff(max_attempts=3, initial_delay=1.5)
    def get_stock_financials(self, query: str) -> Dict[str, Any]:
        """
        财务补充查询（扣非净利润、存货、PE历史分位数等）
        返回清洗后的各列归一化键值对以及原始 markdown answer
        """
        args = {"query": query.strip()}
        res = self._call_mcp_tool(self.STOCK_MCP_URL, "get_stock_financials", args, timeout=25)
        if not res or not isinstance(res, dict):
            return {"answer": "", "tables": []}

        data_obj = res.get("data", {})
        answer_text = data_obj.get("answer", "")
        tables = UnitNormalizer.parse_markdown_tables(answer_text)

        return {
            "answer": answer_text,
            "tables": tables
        }
