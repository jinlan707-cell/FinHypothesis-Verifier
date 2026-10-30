from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Any, Dict

class FactExtractItem(BaseModel):
    fact_id: str = Field(..., description="事实ID，如 FACT-001")
    raw_ref_id: str = Field(..., description="绑定的原始材料ID")
    metric_name: str = Field(..., description="事实或指标名称")
    numeric_value: Optional[float] = Field(None, description="数值")
    unit: str = Field(default="元", description="单位")
    period: str = Field(default="FY", description="报告期")
    exact_quote: str = Field(..., description="原文逐字引文")

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # 兼容 metric, name -> metric_name
            if "metric_name" not in data:
                data["metric_name"] = data.get("metric") or data.get("name") or "未命名指标"

            # 兼容 period, report_period -> period
            if "period" not in data:
                data["period"] = (
                    data.get("report_period")
                    or data.get("report_date")
                    or data.get("date")
                    or "FY"
                )
            
            # 兼容 value -> numeric_value
            if "numeric_value" not in data and "value" in data:
                raw_v = data["value"]
                try:
                    import re
                    nums = re.findall(r"[-+]?\d*\.?\d+", str(raw_v).replace(",", ""))
                    data["numeric_value"] = float(nums[0]) if nums else None
                except Exception:
                    data["numeric_value"] = None

            # 兼容 quote -> exact_quote
            if "exact_quote" not in data and "quote" in data:
                data["exact_quote"] = data["quote"]

            # 兼容 unit 为 None
            if "unit" not in data or data.get("unit") is None:
                data["unit"] = ""

            # 兼容 id -> fact_id
            if "fact_id" not in data and "id" in data:
                data["fact_id"] = str(data["id"])
            elif "fact_id" not in data:
                data["fact_id"] = "FACT-AUTO"
        return data

class FactExtractorOutput(BaseModel):
    extracted_facts: List[FactExtractItem]

    @model_validator(mode="before")
    @classmethod
    def normalize_outer(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "extracted_facts" not in data:
                data["extracted_facts"] = data.get("facts") or data.get("items") or []
        elif isinstance(data, list):
            data = {"extracted_facts": data}
        return data

FACT_EXTRACTOR_SYSTEM_PROMPT = """你是一个严谨的金融事实审计员。
从给定材料中提取关键客观事实，必须输出合法的 JSON 对象，格式如下：
{
  "extracted_facts": [
    {
      "fact_id": "FACT-001",
      "raw_ref_id": "RAW-NOTI-006",
      "metric_name": "营业总收入",
      "numeric_value": 1505.60,
      "unit": "亿元",
      "period": "2023-FY",
      "exact_quote": "报告期内，公司实现营业总收入1,505.60亿元，同比增长18.04%"
    }
  ]
}
【绝对铁律】：exact_quote 必须是一字不差地从对应材料的原文中截取，保留所有标点符号和小数点！
"""
