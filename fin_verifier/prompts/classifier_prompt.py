from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Any
from fin_verifier.core.models import Polarity, SourceType

class EvidenceClassificationItem(BaseModel):
    evidence_id: str
    sub_id: str = "SUB-01"
    title: str
    summary: str
    polarity: Polarity = Polarity.SUPPORT
    source_type: SourceType = SourceType.STATUTORY_NOTICE
    inherited_source_ids: List[str] = Field(default_factory=list)
    fact_ids: List[str] = Field(default_factory=list)
    computation_id: Optional[str] = None
    exact_quote: Optional[str] = None
    weaked_sub_aspect: Optional[str] = None
    unverifiable_reason: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # 兼容 card_id -> evidence_id
            if "evidence_id" not in data:
                data["evidence_id"] = data.get("card_id") or data.get("id") or "EVID-AUTO"
            
            # 兼容 sub_id
            if "sub_id" not in data:
                data["sub_id"] = data.get("hypothesis_id") or "SUB-01"

            # 兼容 polarity
            if "polarity" in data and isinstance(data["polarity"], str):
                p_upper = data["polarity"].upper()
                if "SUPPORT" in p_upper:
                    data["polarity"] = Polarity.SUPPORT
                elif "AGAINST" in p_upper or "REFUTE" in p_upper:
                    data["polarity"] = Polarity.AGAINST
                else:
                    data["polarity"] = Polarity.UNVERIFIABLE

            # 兼容 source_type
            if "source_type" in data and isinstance(data["source_type"], str):
                st = data["source_type"].upper()
                if "NOTICE" in st:
                    data["source_type"] = SourceType.STATUTORY_NOTICE
                elif "FINANCIAL" in st or "STATEMENT" in st:
                    data["source_type"] = SourceType.FINANCIAL_STATEMENTS
                elif "TABLE" in st or "IFIND" in st:
                    data["source_type"] = SourceType.IFIND_TABLE_SYNTHESIS
                elif "NEWS" in st:
                    data["source_type"] = SourceType.NEWS_SUMMARY
                elif "COMPUT" in st or "DERIVED" in st:
                    data["source_type"] = SourceType.DERIVED_COMPUTATION
                else:
                    data["source_type"] = SourceType.STATUTORY_NOTICE

            # 兼容列表空值
            if not data.get("inherited_source_ids"):
                data["inherited_source_ids"] = []
            if not data.get("fact_ids"):
                data["fact_ids"] = []
        return data

class ClassifierOutput(BaseModel):
    evidence_items: List[EvidenceClassificationItem]

    @model_validator(mode="before")
    @classmethod
    def normalize_outer(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "evidence_items" not in data:
                data["evidence_items"] = data.get("evidences") or data.get("cards") or data.get("items") or []
        elif isinstance(data, list):
            data = {"evidence_items": data}
        return data

CLASSIFIER_SYSTEM_PROMPT = """你是一个遵循【审慎归因】原则的金融证据组织官。
请将已核验事实与确定性计算指标归纳为证据卡片，归入对应的子假设（sub_id）。
【归类铁律】：
1. 极性（polarity）必须严格限定为 SUPPORT（支持）、AGAINST（反对）、UNVERIFIABLE（无法验证）；
2. inherited_source_ids 严格只能填入实际提供的 RAW-* 编号；
3. fact_ids 严格只能填入实际提供的 FACT-* 编号；
4. 若为 AGAINST 证据，必须说明 weaked_sub_aspect；若为 UNVERIFIABLE 证据，必须说明 unverifiable_reason；
5. 证据支持到哪，结论走到哪，严禁凭空超范围推论。

必须输出合法 JSON 对象：
{
  "evidence_items": [
    {
      "evidence_id": "EVID-001",
      "sub_id": "SUB-01",
      "title": "高端产品高增与营收占比提升事实",
      "summary": "分析期内核心高端系列产品收入实现大幅增长，占主营业务比重显著扩大",
      "polarity": "SUPPORT",
      "source_type": "STATUTORY_NOTICE",
      "inherited_source_ids": ["RAW-NOTI-001"],
      "fact_ids": ["FACT-001"],
      "exact_quote": "高端产品营业收入同比增长..."
    }
  ]
}
"""
