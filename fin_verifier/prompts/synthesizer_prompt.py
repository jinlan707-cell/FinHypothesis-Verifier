from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Any
from fin_verifier.core.models import VerdictStatus, ConfidenceLevel, FalsificationTrigger, TriggerType

class SynthesizerOutput(BaseModel):
    verdict_status: VerdictStatus = VerdictStatus.PARTIALLY_SUPPORTED
    confidence_level: ConfidenceLevel = ConfidenceLevel.MEDIUM
    verdict_headline: str = ""
    facts_summary: List[str] = Field(default_factory=list)
    inferences_summary: List[str] = Field(default_factory=list)
    uncertainties_summary: List[str] = Field(default_factory=list)
    conflict_resolution: str = ""
    falsification_triggers: List[FalsificationTrigger] = Field(default_factory=list)
    cited_evidence_ids: List[str] = Field(default_factory=list)
    compliance_disclaimer: str = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # 兼容 verdict_headline
            if "verdict_headline" not in data:
                data["verdict_headline"] = (
                    data.get("headline") or data.get("title") or data.get("conclusion") or ""
                )

            # 兼容 verdict_status
            if "verdict_status" in data and isinstance(data["verdict_status"], str):
                v_str = data["verdict_status"].upper()
                if "PARTIAL" in v_str:
                    data["verdict_status"] = VerdictStatus.PARTIALLY_SUPPORTED
                elif "SUPPORT" in v_str:
                    data["verdict_status"] = VerdictStatus.SUPPORTED
                elif "REFUT" in v_str:
                    data["verdict_status"] = VerdictStatus.REFUTED
                else:
                    data["verdict_status"] = VerdictStatus.UNVERIFIABLE

            # 兼容 confidence_level
            if "confidence_level" in data and isinstance(data["confidence_level"], str):
                c_str = data["confidence_level"].upper()
                if "HIGH" in c_str:
                    data["confidence_level"] = ConfidenceLevel.HIGH
                elif "LOW" in c_str:
                    data["confidence_level"] = ConfidenceLevel.LOW
                else:
                    data["confidence_level"] = ConfidenceLevel.MEDIUM

            # 兼容 summaries 为 dict 列表的情况，提取纯字符串
            def _clean_str_list(lst: Any) -> List[str]:
                if not isinstance(lst, list):
                    return [str(lst)] if lst else []
                res = []
                for item in lst:
                    if isinstance(item, str):
                        res.append(item)
                    elif isinstance(item, dict):
                        text = item.get("content") or item.get("text") or item.get("statement") or str(item)
                        res.append(str(text))
                    else:
                        res.append(str(item))
                return res

            if "facts_summary" in data:
                data["facts_summary"] = _clean_str_list(data["facts_summary"])
            if "inferences_summary" in data:
                data["inferences_summary"] = _clean_str_list(data["inferences_summary"])
            if "uncertainties_summary" in data:
                data["uncertainties_summary"] = _clean_str_list(data["uncertainties_summary"])

            # 兼容 conflict_resolution
            if "conflict_resolution" not in data:
                data["conflict_resolution"] = (
                    data.get("conflict_analysis") or data.get("resolution") or ""
                )

            # 兼容 falsification_triggers 里的字段
            raw_trigs = data.get("falsification_triggers") or data.get("triggers") or []
            clean_trigs = []
            for t in raw_trigs:
                if isinstance(t, dict):
                    t_type_str = str(t.get("trigger_type", "")).upper()
                    t_type = (
                        TriggerType.HISTORICAL_ATTRIBUTION
                        if "HISTOR" in t_type_str
                        else TriggerType.FUTURE_SUSTAINABILITY
                    )
                    cond = (
                        t.get("condition_description")
                        or t.get("condition")
                        or t.get("description")
                        or ""
                    )
                    basis = (
                        t.get("analytical_basis")
                        or t.get("basis")
                        or t.get("reason")
                        or ""
                    )
                    clean_trigs.append(FalsificationTrigger(
                        trigger_type=t_type,
                        condition_description=cond,
                        observation_metric=t.get("observation_metric") or t.get("metric"),
                        analytical_basis=basis
                    ))
            
            # 严格保留模型生成的真实反转条件，严禁在此自动塞入硬编码反转条件洗白门禁！
            data["falsification_triggers"] = clean_trigs

            # 兼容 compliance_disclaimer
            if not data.get("compliance_disclaimer"):
                data["compliance_disclaimer"] = (
                    data.get("disclaimer") or data.get("compliance_notes") or ""
                )

        return data

SYNTHESIZER_SYSTEM_PROMPT = """你是一个顶级客观中立的金融事实合规审查与报告合成官。
你的职责是根据已核验事实与三分类证据卡片合成客观中立的投研证伪报告。
【铁律】：
1. 标的中立与审慎归因：证据支持到哪，结论走到哪。缺乏经审计的细分产品/分部利润贡献拆分时，最高维持“部分支持（PARTIALLY_SUPPORTED）”，严禁断言某一因素为“唯一决定性驱动”；
2. 反转触发条件双轨要求：必须同时包含 HISTORICAL_ATTRIBUTION（历史归因推翻条件）与 FUTURE_SUSTAINABILITY（未来持续性失效条件），且每条必须提供实质性 analytical_basis，严禁编造无依据的数字阈值；
3. cited_evidence_ids 必须且只能包含本次提示词中明确提供的有效证据 ID，严禁凭空生成幻觉 ID；
4. 合规边界：严禁出现买入/卖出/目标价/加仓/满仓等投顾荐股词，免责声明必须明确说明基于公开事实对账、不构成买卖建议。

必须输出合法 JSON 对象（严格遵守以下 Schema 格式，内容根据实际标的动态生成）：
{
  "verdict_status": "PARTIALLY_SUPPORTED",
  "confidence_level": "MEDIUM",
  "verdict_headline": "标的盈利能力跃升与高端产品结构升级假设基本一致，但缺乏细分产品毛利直接审计拆解",
  "facts_summary": ["2023年实现营业收入89.15亿元，同比增长65.23%", "2023年研发费用达7.52亿元，同比增长35.12%"],
  "inferences_summary": ["高端产品出货比重扩张有力支撑了主营毛利率和营业收入高增"],
  "uncertainties_summary": ["未能获取各细分速率产品经审计的独立利润增量贡献拆解"],
  "conflict_resolution": "在缺乏直接分部毛利审计数据前，审慎认定为部分支持，防范过度归因",
  "falsification_triggers": [
    {
      "trigger_type": "HISTORICAL_ATTRIBUTION",
      "condition_description": "若后续披露证实低端产品放量对利润增量的直接贡献大于高端升级，则推翻历史归因",
      "observation_metric": "分部毛利与利润贡献拆解",
      "analytical_basis": "现有结论建立在产品单价与结构升级假说上，若直接利润贡献相反则推翻"
    },
    {
      "trigger_type": "FUTURE_SUSTAINABILITY",
      "condition_description": "若后续季度行业竞争加剧导致高端产品毛利率出现不可逆下滑，则未来盈利持续性弱化",
      "observation_metric": "高端产品毛利率 / 综合毛利率",
      "analytical_basis": "该盈利跃升模式高度依赖高端产品高技术溢价与高毛利率维持"
    }
  ],
  "cited_evidence_ids": ["EVID-001"],
  "compliance_disclaimer": "本报告基于上市公司公开披露数据进行中立事实查验与对账，不构成任何投资建议或买卖推荐。"
}
"""
