import pytest
from fin_verifier.core.config import mask_secret, TASKS_DATA_DIR, FUYAO_KEY, IFIND_MCP_TOKEN
from fin_verifier.core.models import (
    TaskStatus,
    VerdictStatus,
    ConfidenceLevel,
    SourceType,
    Polarity,
    TriggerType,
    RawRefRecord,
    ExtractedFact,
    ComputedMetric,
    EvidenceItem,
    SubHypothesis,
    FalsificationTrigger,
    FinalReport,
    ResearchTask,
    ClarifyRequest,
    ClarifyResponse,
    VerifyRequest
)

def test_config_masking():
    assert mask_secret("") == "<EMPTY>"
    assert mask_secret("123") == "***"
    assert mask_secret("abcdefghijk") == "abcd...hijk"
    assert TASKS_DATA_DIR.exists()

def test_models_strong_id_closure():
    # 1. 原始材料
    raw = RawRefRecord(
        raw_ref_id="RAW-001",
        source_type=SourceType.STATUTORY_NOTICE,
        title="贵州茅台2023年年度报告",
        publish_date="2024-04-03",
        raw_text="公司实现营业总收入1,505.60亿元，同比增长18.04%。"
    )
    assert raw.raw_ref_id == "RAW-001"

    # 2. 事实抽取
    fact = ExtractedFact(
        fact_id="FACT-001",
        raw_ref_id="RAW-001",
        metric_name="营业总收入",
        numeric_value=1505.60,
        unit="亿元",
        period="2023-FY",
        exact_quote="公司实现营业总收入1,505.60亿元，同比增长18.04%。",
        verified_in_raw=True
    )
    assert fact.raw_ref_id == raw.raw_ref_id

    # 3. 确定性计算
    comp = ComputedMetric(
        computation_id="COMP-001",
        name="营业总收入同比增速",
        value=18.04,
        unit="%",
        scope="FY同比",
        formula="(本期收入 - 上年同期收入) / 上年同期收入 * 100",
        trace="(1505.60 - 1275.54) / 1275.54 * 100 = 18.04%",
        input_fact_ids=["FACT-001"]
    )
    assert fact.fact_id in comp.input_fact_ids

    # 4. 证据卡片
    evid = EvidenceItem(
        evidence_id="EVID-001",
        sub_id="SUB-01",
        title="2023年营收高增事实",
        summary="营业总收入达1505.60亿元，同比增长18.04%",
        polarity=Polarity.SUPPORT,
        source_type=SourceType.STATUTORY_NOTICE,
        inherited_source_ids=[raw.raw_ref_id],
        fact_ids=[fact.fact_id],
        computation_id=comp.computation_id,
        exact_quote=fact.exact_quote
    )
    assert evid.inherited_source_ids == ["RAW-001"]
    assert evid.fact_ids == ["FACT-001"]

    # 5. 子假设
    sub = SubHypothesis(
        id="SUB-01",
        dimension="价格与销量",
        statement="2023年营收增长保持两位数扩张",
        verdict_status=VerdictStatus.SUPPORTED,
        confidence_level=ConfidenceLevel.HIGH,
        supported_evidence_ids=[evid.evidence_id]
    )
    assert evid.evidence_id in sub.supported_evidence_ids

    # 6. 反转触发条件
    trigger = FalsificationTrigger(
        trigger_type=TriggerType.HISTORICAL_ATTRIBUTION,
        condition_description="若经审计分部数据显示销量拉动比例大于提价",
        observation_metric="分部利润贡献率",
        analytical_basis="缺乏拆解毛利前暂定为一致，拆解后若相反则推翻"
    )

    # 7. 最终报告
    report = FinalReport(
        verdict_status=VerdictStatus.PARTIALLY_SUPPORTED,
        confidence_level=ConfidenceLevel.MEDIUM,
        verdict_headline="茅台增长与提价和渠道假说基本一致，但缺乏细分毛利归因数据",
        facts_summary=["2023年营收1505.60亿元"],
        inferences_summary=["直销与提价支撑了增速"],
        uncertainties_summary=["未获得出厂价与零售价分部毛利拆解"],
        conflict_resolution="由于缺乏直接分部毛利，结论限制在部分支持",
        falsification_triggers=[trigger],
        cited_evidence_ids=[evid.evidence_id],
        compliance_disclaimer="本结论基于公开审计披露，不构成投资买卖建议"
    )
    assert report.cited_evidence_ids == ["EVID-001"]

    # 8. 完整持久化任务
    task = ResearchTask(
        task_id="task-test-001",
        status=TaskStatus.COMPLETED,
        starred=False,
        tags=["茅台", "渠道"],
        ticker="600519.SH",
        ticker_name="贵州茅台",
        original_hypothesis="茅台增长靠提价",
        clarified_hypothesis="茅台2023年营收增长主要由提价与直销驱动",
        analysis_start_date="2023-01-01",
        analysis_end_date="2024-12-31",
        data_cutoff_time="2025-01-01",
        latest_financial_period="2024-Q3",
        retrieved_periods=["2023-FY", "2024-Q3"],
        raw_records=[raw],
        extracted_facts=[fact],
        computed_metrics=[comp],
        sub_hypotheses=[sub],
        evidence_items=[evid],
        report=report,
        created_at="2026-10-30T10:00:00",
        updated_at="2026-10-30T10:05:00"
    )

    # 验证序列化与反序列化完整性
    task_json = task.model_dump_json()
    reloaded_task = ResearchTask.model_validate_json(task_json)
    assert reloaded_task.task_id == "task-test-001"
    assert len(reloaded_task.raw_records) == 1
    assert reloaded_task.raw_records[0].raw_ref_id == "RAW-001"
    assert len(reloaded_task.extracted_facts) == 1
    assert reloaded_task.extracted_facts[0].fact_id == "FACT-001"
    assert len(reloaded_task.computed_metrics) == 1
    assert reloaded_task.computed_metrics[0].computation_id == "COMP-001"
    assert reloaded_task.report.verdict_status == VerdictStatus.PARTIALLY_SUPPORTED

def test_api_models():
    req = ClarifyRequest(
        ticker="600519.SH",
        hypothesis="茅台靠提价",
        analysis_start_date="2023-01-01",
        analysis_end_date="2024-12-31"
    )
    assert req.analysis_start_date == "2023-01-01"

    resp = ClarifyResponse(
        ticker="600519.SH",
        ticker_name="贵州茅台",
        clarified_hypothesis="经澄清命题",
        analysis_start_date="2023-01-01",
        analysis_end_date="2024-12-31",
        compliance_notes="合规说明",
        suggested_dimensions=["渠道结构", "价格与销量"]
    )
    assert resp.ticker_name == "贵州茅台"
