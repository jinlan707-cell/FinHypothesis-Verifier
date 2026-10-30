import pytest
from fin_verifier.core.models import (
    ResearchTask,
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
    FinalReport
)
from fin_verifier.core.state_manager import TaskStateManager
from fin_verifier.core.validator import PipelineValidator

def test_atomic_save_and_reload(tmp_path, monkeypatch):
    monkeypatch.setattr("fin_verifier.core.state_manager.TASKS_DATA_DIR", tmp_path)

    task = ResearchTask(
        task_id="task-atomic-01",
        status=TaskStatus.CREATED,
        ticker="600519.SH",
        ticker_name="贵州茅台",
        original_hypothesis="茅台靠提价",
        clarified_hypothesis="茅台增长靠提价",
        analysis_start_date="2023-01-01",
        analysis_end_date="2024-12-31",
        data_cutoff_time="2025-01-01",
        latest_financial_period="2024-Q3",
        created_at="2026-10-30T10:00:00",
        updated_at="2026-10-30T10:00:00"
    )

    TaskStateManager.save_task(task)
    loaded = TaskStateManager.load_task("task-atomic-01")
    assert loaded is not None
    assert loaded.task_id == "task-atomic-01"
    assert loaded.status == TaskStatus.CREATED

    # 测试收藏与标签
    TaskStateManager.set_star_and_tags("task-atomic-01", starred=True, tags=["白酒", "白酒", "重点"])
    updated = TaskStateManager.load_task("task-atomic-01")
    assert updated.starred is True
    assert updated.tags == ["白酒", "重点"]  # 验证标签去重

def test_validator_exact_quote_preserves_punctuation():
    raw = RawRefRecord(
        raw_ref_id="RAW-1",
        source_type=SourceType.STATUTORY_NOTICE,
        title="2023年报",
        raw_text="2023年公司实现营业总收入1,505.60亿元，同比增长18.04%，净利润增幅为19.16%。"
    )

    # 1. 真实包含（含小数点、逗号、百分号） -> 核验通过
    valid_fact = ExtractedFact(
        fact_id="FACT-1",
        raw_ref_id="RAW-1",
        metric_name="营业总收入",
        numeric_value=1505.60,
        unit="亿元",
        period="2023-FY",
        exact_quote="营业总收入1,505.60亿元，同比增长18.04%"
    )
    v_facts, issues = PipelineValidator.validate_facts_against_raw([raw], [valid_fact])
    assert len(v_facts) == 1
    assert len(issues) == 0
    assert valid_fact.verified_in_raw is True

    # 2. 篡改数字（1505.60 篡改为 1600.00） -> 拦截失败
    tampered_fact = ExtractedFact(
        fact_id="FACT-2",
        raw_ref_id="RAW-1",
        metric_name="营业总收入",
        numeric_value=1600.00,
        unit="亿元",
        period="2023-FY",
        exact_quote="营业总收入1,600.00亿元"
    )
    v_facts, issues = PipelineValidator.validate_facts_against_raw([raw], [tampered_fact])
    assert len(v_facts) == 0
    assert len(issues) == 1
    assert "防伪核验失败" in issues[0]

def test_validator_pre_synthesis_gate():
    raw = RawRefRecord(
        raw_ref_id="RAW-1",
        source_type=SourceType.STATUTORY_NOTICE,
        title="2023年年度报告",
        raw_text="公司2023年实现营业收入1500亿元。"
    )
    fact = ExtractedFact(
        fact_id="FACT-1",
        raw_ref_id="RAW-1",
        metric_name="营业收入",
        numeric_value=1500.0,
        unit="亿元",
        period="2023-FY",
        exact_quote="营业收入1500亿元",
        verified_in_raw=True
    )
    comp = ComputedMetric(
        computation_id="COMP-1",
        name="收入增速",
        value=15.0,
        unit="%",
        scope="FY同比",
        formula="x",
        trace="x",
        input_fact_ids=["FACT-1"]
    )
    evid = EvidenceItem(
        evidence_id="EVID-1",
        sub_id="SUB-1",
        title="营收证据",
        summary="营收1500亿",
        polarity=Polarity.SUPPORT,
        source_type=SourceType.STATUTORY_NOTICE,
        inherited_source_ids=["RAW-1"],
        fact_ids=["FACT-1"],
        computation_id="COMP-1"
    )

    # 正常通过
    res = PipelineValidator.validate_before_synthesis([raw], [fact], [comp], [evid])
    assert res.is_valid is True

    # 若计算引用了不存在的 fact_id
    comp_bad = ComputedMetric(
        computation_id="COMP-BAD",
        name="虚假计算",
        value=10.0,
        unit="%",
        scope="FY",
        formula="x",
        trace="x",
        input_fact_ids=["FACT-GHOST"]
    )
    res_bad = PipelineValidator.validate_before_synthesis([raw], [fact], [comp_bad], [evid])
    assert res_bad.is_valid is False
    assert any("FACT-GHOST" in s for s in res_bad.issues)

def test_validator_post_synthesis_gate():
    evid = EvidenceItem(
        evidence_id="EVID-1",
        sub_id="SUB-1",
        title="营收证据",
        summary="营收高增",
        polarity=Polarity.SUPPORT,
        source_type=SourceType.STATUTORY_NOTICE,
        inherited_source_ids=["RAW-1"],
        fact_ids=["FACT-1"]
    )

    trig_hist = FalsificationTrigger(
        trigger_type=TriggerType.HISTORICAL_ATTRIBUTION,
        condition_description="若经审计销量贡献超过提价",
        observation_metric="分部毛利",
        analytical_basis="缺乏直接分部毛利前暂定一致，若有反向数据则推翻"
    )
    trig_future = FalsificationTrigger(
        trigger_type=TriggerType.FUTURE_SUSTAINABILITY,
        condition_description="若后续季度直销占比发生实质性下滑",
        observation_metric="直销收入占比",
        analytical_basis="渠道驱动假设依赖直销占比维持高位"
    )

    # 合法报告
    valid_report = FinalReport(
        verdict_status=VerdictStatus.PARTIALLY_SUPPORTED,
        confidence_level=ConfidenceLevel.MEDIUM,
        verdict_headline="增长与渠道提价驱动假说一致",
        facts_summary=["事实1"],
        inferences_summary=["推论1"],
        uncertainties_summary=["存在分部毛利缺口"],
        conflict_resolution="调和说明",
        falsification_triggers=[trig_hist, trig_future],
        cited_evidence_ids=["EVID-1"],
        compliance_disclaimer="本系统仅对公开事实进行对账，不构成投资建议"
    )
    res = PipelineValidator.validate_after_synthesis(valid_report, [evid])
    assert res.is_valid is True

    # 违规投资建议敏感词拦截测试
    bad_report = valid_report.model_copy(deep=True)
    bad_report.verdict_headline = "增长稳健，建议买入贵州茅台，目标价2200元！"
    res_bad = PipelineValidator.validate_after_synthesis(bad_report, [evid])
    assert res_bad.is_valid is False
    assert any("敏感词" in s for s in res_bad.issues)

def test_validator_numeric_tampering_with_real_quote():
    # 测试原文引文真实存在，但提取的 numeric_value 被人为篡改的场景
    raw = RawRefRecord(
        raw_ref_id="RAW-10",
        source_type=SourceType.STATUTORY_NOTICE,
        title="2023年年度报告",
        raw_text="2023年公司营业收入实现1505.60亿元，归母净利润实现747.34亿元。"
    )
    # 引文一字不差截取真句子，但 numeric_value 故意伪造为 99999.0
    tampered_fact = ExtractedFact(
        fact_id="FACT-FAKE-NUM",
        raw_ref_id="RAW-10",
        metric_name="营业收入",
        numeric_value=99999.0,
        unit="亿元",
        period="2023-FY",
        exact_quote="2023年公司营业收入实现1505.60亿元"
    )
    v_facts, issues = PipelineValidator.validate_facts_against_raw([raw], [tampered_fact])
    assert len(v_facts) == 0
    assert len(issues) == 1
    assert "数值防伪核验失败" in issues[0]

def test_validator_period_mismatch():
    # 测试材料发布于 2021 年，但抽取的事实伪造为 2024 年
    raw = RawRefRecord(
        raw_ref_id="RAW-OLD",
        source_type=SourceType.STATUTORY_NOTICE,
        title="2021年年度报告",
        publish_date="2022-04-01",
        raw_text="2021年公司净利润实现524.60亿元。"
    )
    mismatched_fact = ExtractedFact(
        fact_id="FACT-MIS-PERIOD",
        raw_ref_id="RAW-OLD",
        metric_name="净利润",
        numeric_value=524.60,
        unit="亿元",
        period="2024-FY",
        exact_quote="净利润实现524.60亿元"
    )
    v_facts, issues = PipelineValidator.validate_facts_against_raw([raw], [mismatched_fact])
    assert len(v_facts) == 0
    assert len(issues) == 1
    assert "报告期核验失败" in issues[0]

def test_synthesizer_output_no_auto_injection_backdoor():
    from fin_verifier.prompts.synthesizer_prompt import SynthesizerOutput
    # 测试模型未输出未来反转条件时，模型解析器不会静默塞入硬编码假反转条件洗白门禁
    raw_data = {
        "verdict_status": "SUPPORTED",
        "confidence_level": "HIGH",
        "verdict_headline": "真实研报结论",
        "facts_summary": ["事实A"],
        "inferences_summary": ["推论A"],
        "uncertainties_summary": [],
        "conflict_resolution": "无冲突",
        "falsification_triggers": [
            {
                "trigger_type": "HISTORICAL_ATTRIBUTION",
                "condition_description": "历史推翻条件",
                "observation_metric": "分部毛利",
                "analytical_basis": "充分依据"
            }
        ],
        "cited_evidence_ids": ["EVID-1"]
    }
    output = SynthesizerOutput.model_validate(raw_data)
    # 确认没有被偷塞 FUTURE_SUSTAINABILITY
    assert len(output.falsification_triggers) == 1
    assert output.falsification_triggers[0].trigger_type == TriggerType.HISTORICAL_ATTRIBUTION

def test_cleanup_orphan_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr("fin_verifier.core.state_manager.TASKS_DATA_DIR", tmp_path)

    # 1. 创建处于中途运行态的任务 (RETRIEVING, SYNTHESIZING)
    task_running = ResearchTask(
        task_id="t-running",
        status=TaskStatus.RETRIEVING,
        ticker="300308.SZ",
        ticker_name="中际旭创",
        original_hypothesis="命题",
        clarified_hypothesis="命题",
        analysis_start_date="2023-01-01",
        analysis_end_date="2024-12-31",
        data_cutoff_time="2025-01-01",
        latest_financial_period="",
        created_at="2026-10-30T10:00:00",
        updated_at="2026-10-30T10:00:00"
    )
    TaskStateManager.save_task(task_running)

    task_completed = ResearchTask(
        task_id="t-completed",
        status=TaskStatus.COMPLETED,
        ticker="300308.SZ",
        ticker_name="中际旭创",
        original_hypothesis="命题",
        clarified_hypothesis="命题",
        analysis_start_date="2023-01-01",
        analysis_end_date="2024-12-31",
        data_cutoff_time="2025-01-01",
        latest_financial_period="2024-Q3",
        created_at="2026-10-30T10:00:00",
        updated_at="2026-10-30T10:00:00"
    )
    TaskStateManager.save_task(task_completed)

    # 2. 执行重启收敛清理
    cleaned = TaskStateManager.cleanup_orphan_tasks()
    assert cleaned == 1

    # 3. 验证中途任务被标记为 FAILED，已完成任务保持不变
    reloaded_running = TaskStateManager.load_task("t-running")
    assert reloaded_running.status == TaskStatus.FAILED
    assert "服务重启导致未完成任务中断" in reloaded_running.error_message

    reloaded_completed = TaskStateManager.load_task("t-completed")
    assert reloaded_completed.status == TaskStatus.COMPLETED
