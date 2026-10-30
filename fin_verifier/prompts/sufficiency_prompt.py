from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Any

class ReplenishQuery(BaseModel):
    sub_id: str = "SUB-01"
    target_data_type: str = "NOTICE"
    query: str
    reason: str = "补充证据"

class SufficiencyOutput(BaseModel):
    is_sufficient: bool = Field(default=True)
    needs_replenish: bool = Field(default=False)
    gap_notes: List[str] = Field(default_factory=list)
    replenish_queries: List[ReplenishQuery] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "is_sufficient" not in data:
                status = str(data.get("review_status") or "").lower()
                data["is_sufficient"] = "sufficient" in status and "insufficient" not in status
            
            if "needs_replenish" not in data:
                queries = data.get("replenish_queries") or data.get("queries") or []
                data["needs_replenish"] = len(queries) > 0

            if "gap_notes" not in data:
                data["gap_notes"] = data.get("gaps") or data.get("gap_list") or []

            # 兼容 replenish_queries 为纯字符串列表的情况
            if "replenish_queries" in data and isinstance(data["replenish_queries"], list):
                clean_queries = []
                for q in data["replenish_queries"]:
                    if isinstance(q, str):
                        clean_queries.append({"query": q, "sub_id": "SUB-01", "target_data_type": "NOTICE", "reason": "定向补充证据"})
                    elif isinstance(q, dict):
                        clean_queries.append(q)
                data["replenish_queries"] = clean_queries
        return data

SUFFICIENCY_SYSTEM_PROMPT = """你是一个研究质量审查员。
评估当前事实与计算指标对各子假设的覆盖度，输出 JSON 对象：
{
  "is_sufficient": true,
  "needs_replenish": false,
  "gap_notes": ["客观数据缺口说明"],
  "replenish_queries": []
}
"""
