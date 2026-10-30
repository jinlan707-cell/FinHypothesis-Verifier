from enum import Enum
from typing import Optional, Any, Literal
from pydantic import BaseModel, Field

# ==============================================================================
# 1. 核心枚举类型 (Enums)
# ==============================================================================

class TaskStatus(str, Enum):
    CREATED = "CREATED"
    DECOMPOSING = "DECOMPOSING"
    RETRIEVING = "RETRIEVING"
    EXTRACTING_AND_COMPUTING = "EXTRACTING_AND_COMPUTING"
    SUFFICIENCY_CHECKING = "SUFFICIENCY_CHECKING"
    REPLENISHING = "REPLENISHING"
    CLASSIFYING = "CLASSIFYING"
    VALIDATING = "VALIDATING"
    SYNTHESIZING = "SYNTHESIZING"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"  # 存在客观缺口时的降级成功态
    FAILED = "FAILED"                            # 伪造、无效或无法产出有效报告

class VerdictStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    REFUTED = "REFUTED"
    UNVERIFIABLE = "UNVERIFIABLE"

class ConfidenceLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class SourceType(str, Enum):
    STATUTORY_NOTICE = "STATUTORY_NOTICE"          # 上市公司法定公告
    FINANCIAL_STATEMENTS = "FINANCIAL_STATEMENTS"  # 扶摇结构化财报
    IFIND_TABLE_SYNTHESIS = "IFIND_TABLE_SYNTHESIS"# iFinD 汇总查询表格
    NEWS_SUMMARY = "NEWS_SUMMARY"                  # 新闻或说明会纪要
    DERIVED_COMPUTATION = "DERIVED_COMPUTATION"    # 确定性计算推导

class Polarity(str, Enum):
    SUPPORT = "SUPPORT"
    AGAINST = "AGAINST"
    UNVERIFIABLE = "UNVERIFIABLE"

class TriggerType(str, Enum):
    HISTORICAL_ATTRIBUTION = "HISTORICAL_ATTRIBUTION"
    FUTURE_SUSTAINABILITY = "FUTURE_SUSTAINABILITY"


# ==============================================================================
# 2. 溯源闭环与领域实体模型 (Core Domain Entities)
# ==============================================================================

class RawRefRecord(BaseModel):
    """原始材料凭证记录 (Node: raw_ref_id)"""
    raw_ref_id: str = Field(..., description="唯一原始材料ID，例如 RAW-NOTICE-001")
    source_type: SourceType = Field(..., description="材料数据来源类型")
    title: str = Field(..., description="公告/报表/资讯标题")
    publish_date: Optional[str] = Field(None, description="发布日期，格式 YYYY-MM-DD")
    raw_text: str = Field(..., description="原始完整文本片段，严禁去除标点符号")


class ExtractedFact(BaseModel):
    """从原始材料抽取并验证的原子事实 (Node: fact_id)"""
    fact_id: str = Field(..., description="唯一事实ID，例如 FACT-001")
    raw_ref_id: str = Field(..., description="绑定的原始材料ID")
    metric_name: str = Field(..., description="指标/事实名称")
    numeric_value: Optional[float] = Field(None, description="数值（统一转换为亿元或百分比原始浮点数）")
    unit: str = Field(..., description="单位，如 '亿元', '%', '吨'")
    period: str = Field(..., description="报告期，如 '2023-FY', '2024-Q3'")
    exact_quote: str = Field(..., description="原始材料逐字真实引文，必须严格保留标点符号")
    verified_in_raw: bool = Field(False, description="是否通过代码级原文子串包含性核验")


class ComputedMetric(BaseModel):
    """通过确定性计算器生成的推导指标 (Node: computation_id)"""
    computation_id: str = Field(..., description="唯一计算项ID，例如 COMP-001")
    name: str = Field(..., description="推导指标名称，如 '直销收入占比'")
    value: float = Field(..., description="计算结果数值")
    unit: str = Field(..., description="结果单位，如 '%', '亿元'")
    scope: str = Field(..., description="计算口径与范围说明，如 '单季/累计/同比'")
    formula: str = Field(..., description="计算公式明细，如 '(直销收入 / 营业收入) * 100'")
    trace: str = Field(..., description="代入具体数值的运算链路，如 '(672.33 / 1476.94) * 100 = 45.52%'")
    input_fact_ids: list[str] = Field(default_factory=list, description="计算依赖的输入事实ID列表")


class EvidenceItem(BaseModel):
    """证据卡片 (Node: evidence_id)"""
    evidence_id: str = Field(..., description="唯一证据ID，例如 EVID-001")
    sub_id: str = Field(..., description="绑定的子假设ID，例如 SUB-01")
    title: str = Field(..., description="证据卡片标题")
    summary: str = Field(..., description="事实与推导摘要说明")
    polarity: Polarity = Field(..., description="证据极性：支持、反对、无法验证")
    source_type: SourceType = Field(..., description="证据主来源类型")
    inherited_source_ids: list[str] = Field(..., description="严格只存引用的 raw_ref_id 列表")
    fact_ids: list[str] = Field(default_factory=list, description="严格只存绑定的 fact_id 列表")
    computation_id: Optional[str] = Field(None, description="若为计算推导证据，关联的 computation_id")
    exact_quote: Optional[str] = Field(None, description="核心原文摘录")
    weaked_sub_aspect: Optional[str] = Field(None, description="反对证据具体削弱的子维度（审慎归因）")
    unverifiable_reason: Optional[str] = Field(None, description="无法验证的原因说明")


class SubHypothesis(BaseModel):
    """可验证子假设 (Node: sub_id)"""
    id: str = Field(..., description="子假设唯一ID，如 SUB-01")
    dimension: str = Field(..., description="所属维度，如 '渠道结构', '价格与销量', '财务质量', '行业比较'")
    statement: str = Field(..., description="子假设陈述")
    verdict_status: VerdictStatus = Field(default=VerdictStatus.UNVERIFIABLE, description="子假设判定结论")
    confidence_level: ConfidenceLevel = Field(default=ConfidenceLevel.LOW, description="置信度")
    supported_evidence_ids: list[str] = Field(default_factory=list, description="支持证据ID列表")
    against_evidence_ids: list[str] = Field(default_factory=list, description="反对证据ID列表")
    unverifiable_evidence_ids: list[str] = Field(default_factory=list, description="无法验证证据ID列表")
    evidence_gap_note: Optional[str] = Field(None, description="证据缺口记录说明")


class FalsificationTrigger(BaseModel):
    """反转触发条件（严格区分历史归因与未来持续性）"""
    trigger_type: TriggerType = Field(..., description="反转类型")
    condition_description: str = Field(..., description="触发条件描述")
    observation_metric: Optional[str] = Field(None, description="重点观察指标")
    analytical_basis: str = Field(..., description="推翻或失效分析依据，不得无依据编造数值阈值")

    from pydantic import model_validator

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # 兼容 trigger_type
            if "trigger_type" not in data:
                raw_t = str(data.get("type") or data.get("trigger") or "").upper()
                if "HISTOR" in raw_t:
                    data["trigger_type"] = TriggerType.HISTORICAL_ATTRIBUTION
                else:
                    data["trigger_type"] = TriggerType.FUTURE_SUSTAINABILITY
            elif isinstance(data["trigger_type"], str):
                raw_t = data["trigger_type"].upper()
                if "HISTOR" in raw_t:
                    data["trigger_type"] = TriggerType.HISTORICAL_ATTRIBUTION
                else:
                    data["trigger_type"] = TriggerType.FUTURE_SUSTAINABILITY

            # 兼容 condition_description
            if "condition_description" not in data:
                data["condition_description"] = (
                    data.get("condition") or data.get("desc") or data.get("description") or "未说明反转条件"
                )

            # 兼容 analytical_basis
            if "analytical_basis" not in data:
                data["analytical_basis"] = (
                    data.get("basis") or data.get("analytical_basis") or data.get("reason") or "基于审慎因果检验"
                )
        return data


class FinalReport(BaseModel):
    """最终核验结构化报告"""
    verdict_status: VerdictStatus = Field(..., description="总结论判定状态（与置信度分字段）")
    confidence_level: ConfidenceLevel = Field(..., description="综合置信度")
    verdict_headline: str = Field(..., description="结论一句话标题摘要")
    facts_summary: list[str] = Field(default_factory=list, description="已核验的核心事实列表")
    inferences_summary: list[str] = Field(default_factory=list, description="基于事实的审慎推断")
    uncertainties_summary: list[str] = Field(default_factory=list, description="存在的数据与推断缺口说明")
    conflict_resolution: str = Field(..., description="证据冲突化解与调和说明")
    falsification_triggers: list[FalsificationTrigger] = Field(default_factory=list, description="反转触发条件列表")
    cited_evidence_ids: list[str] = Field(default_factory=list, description="报告引用的全部证据ID")
    compliance_disclaimer: str = Field(..., description="合规免责声明，明确不构成投资建议")


class ResearchTask(BaseModel):
    """持久化完整研究任务实体 (Single Source of Truth)"""
    task_id: str = Field(..., description="唯一任务ID，例如 task-20261030-001")
    status: TaskStatus = Field(..., description="任务当前流转状态")
    starred: bool = Field(False, description="是否被用户收藏")
    tags: list[str] = Field(default_factory=list, description="用户自定义标签")
    error_message: Optional[str] = Field(None, description="失败或异常信息")
    
    ticker: str = Field(..., description="标准证券代码，如 '600519.SH'")
    ticker_name: str = Field(..., description="标的中文名称，如 '贵州茅台'")
    original_hypothesis: str = Field(..., description="用户输入的初始命题")
    clarified_hypothesis: str = Field(..., description="经合规与因果澄清后的规范命题")
    
    analysis_start_date: str = Field(..., description="分析起始日期 YYYY-MM-DD")
    analysis_end_date: str = Field(..., description="分析终止日期 YYYY-MM-DD")
    data_cutoff_time: str = Field(..., description="信息检索截至时间")
    latest_financial_period: str = Field(..., description="最新已披露财务报告期")
    retrieved_periods: list[str] = Field(default_factory=list, description="实际取数报告期列表")
    meta: dict[str, Any] = Field(default_factory=dict, description="执行元数据，如阶段耗时、重试记录等")

    # 显式持久化完整材料与中间事实，服务重启后沿ID完整回溯
    raw_records: list[RawRefRecord] = Field(default_factory=list)
    extracted_facts: list[ExtractedFact] = Field(default_factory=list)
    computed_metrics: list[ComputedMetric] = Field(default_factory=list)
    sub_hypotheses: list[SubHypothesis] = Field(default_factory=list)
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    report: Optional[FinalReport] = None
    inquiries: list[dict[str, Any]] = Field(default_factory=list)
    
    created_at: str = Field(..., description="创建时间 ISO 8601")
    updated_at: str = Field(..., description="更新时间 ISO 8601")


# ==============================================================================
# 3. HTTP 请求与响应模型 (API Request/Response Schemas)
# ==============================================================================

class DemoItem(BaseModel):
    id: str
    ticker: str
    ticker_name: str
    title: str
    hypothesis: str
    analysis_start_date: str
    analysis_end_date: str
    description: str

class TickerItem(BaseModel):
    thscode: str
    name: str
    market: Optional[str] = None

class ClarifyRequest(BaseModel):
    ticker: str
    hypothesis: str
    analysis_start_date: Optional[str] = None
    analysis_end_date: Optional[str] = None
    period_range: Optional[str] = None  # 兼容旧字段

class ClarifyResponse(BaseModel):
    ticker: str
    ticker_name: str
    clarified_hypothesis: str
    analysis_start_date: str
    analysis_end_date: str
    compliance_notes: str
    suggested_dimensions: list[str]

class VerifyRequest(BaseModel):
    ticker: str = Field(..., description="标准证券代码，如 300308.SZ")
    ticker_name: Optional[str] = Field(None, description="公司标准简称，如 中际旭创")
    hypothesis: Optional[str] = Field(None, description="命题文本（兼容字段）")
    original_hypothesis: Optional[str] = Field(None, description="原始未澄清命题")
    clarified_hypothesis: Optional[str] = Field(None, description="经 Step 0 澄清润色并经用户确认的命题")
    analysis_start_date: Optional[str] = Field(None, description="分析起始日期 YYYY-MM-DD")
    analysis_end_date: Optional[str] = Field(None, description="分析终止日期 YYYY-MM-DD")
    period_range: Optional[str] = None  # 兼容旧字段

class VerifyResponse(BaseModel):
    task_id: str
    status: TaskStatus
    message: str

class TaskSummaryItem(BaseModel):
    task_id: str
    status: TaskStatus
    starred: bool
    tags: list[str]
    ticker: str
    ticker_name: str
    hypothesis: str
    verdict_status: Optional[VerdictStatus] = None
    confidence_level: Optional[ConfidenceLevel] = None
    created_at: str
    updated_at: str

class StarTaskRequest(BaseModel):
    starred: Optional[bool] = None
    tags: Optional[list[str]] = None

class CompareResponse(BaseModel):
    task_a: ResearchTask
    task_b: ResearchTask
    comparison_summary: dict[str, Any]

class InquireRequest(BaseModel):
    question: str

class InquireResponse(BaseModel):
    question: str
    answer: str
    cited_evidence_ids: list[str]
    created_at: str
