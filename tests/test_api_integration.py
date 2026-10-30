import pytest
from fastapi.testclient import TestClient
import fin_verifier.main
from fin_verifier.main import app
from fin_verifier.core.models import TaskStatus, VerdictStatus, ConfidenceLevel, FinalReport, ResearchTask
from fin_verifier.core.state_manager import TaskStateManager
from fin_verifier.prompts.clarifier_prompt import ClarifyOutput

client = TestClient(app)

def test_api_demos():
    res = client.get("/api/demos")
    assert res.status_code == 200
    demos = res.json()
    assert len(demos) >= 3
    assert any(d["ticker"] == "300308.SZ" for d in demos)
    # 确认不返回预设答案
    for d in demos:
        assert "verdict_status" not in d

def test_api_ticker_search_offline(monkeypatch):
    monkeypatch.setattr(
        fin_verifier.main.fuyao_client,
        "search_ticker",
        lambda q: [{"thscode": "300308.SZ", "name": "中际旭创", "market": "SZ"}]
    )
    res = client.get("/api/tickers/search", params={"q": "中际旭创"})
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 1
    assert data[0]["thscode"] == "300308.SZ"
    assert data[0]["name"] == "中际旭创"

@pytest.mark.live
def test_api_ticker_search_live():
    res = client.get("/api/tickers/search", params={"q": "中际旭创"})
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 1
    assert any("300308" in item["thscode"] for item in data)

def test_api_clarify_offline(monkeypatch):
    mock_output = ClarifyOutput(
        clarified_hypothesis="算力需求拉动下中际旭创高端光模块放量驱动毛利率与净利润高增",
        analysis_start_date="2023-01-01",
        analysis_end_date="2024-12-31",
        suggested_dimensions=["盈利能力与毛利率", "产品结构与高端放量", "客户与下游需求"],
        compliance_notes="本研究基于公开披露历史财务与公告进行实证检验，不构成任何投资建议。"
    )
    monkeypatch.setattr(
        fin_verifier.main.llm_client,
        "call_structured",
        lambda *args, **kwargs: mock_output
    )
    payload = {
        "ticker": "300308.SZ",
        "ticker_name": "中际旭创",
        "hypothesis": "中际旭创800G放量利润大增",
        "analysis_start_date": "2023-01-01",
        "analysis_end_date": "2024-12-31"
    }
    res = client.post("/api/clarify", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "clarified_hypothesis" in data
    assert "compliance_notes" in data
    assert len(data["suggested_dimensions"]) > 0

@pytest.mark.live
def test_api_clarify_live():
    payload = {
        "ticker": "300308.SZ",
        "ticker_name": "中际旭创",
        "hypothesis": "算力需求拉动下，中际旭创盈利能力大幅跃升主要得益于高端光模块产品结构升级",
        "analysis_start_date": "2023-01-01",
        "analysis_end_date": "2024-12-31"
    }
    res = client.post("/api/clarify", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "clarified_hypothesis" in data
    assert "compliance_notes" in data
    assert len(data["suggested_dimensions"]) > 0

def test_api_verify_validation_rules(tmp_path, monkeypatch):
    monkeypatch.setattr("fin_verifier.core.state_manager.TASKS_DATA_DIR", tmp_path)

    # 1. 起止日期颠倒拦截 (start_date >= end_date)
    res_bad_date = client.post("/api/verify", json={
        "ticker": "300308.SZ",
        "ticker_name": "中际旭创",
        "hypothesis": "合法命题描述文本",
        "analysis_start_date": "2024-12-31",
        "analysis_end_date": "2023-01-01"
    })
    assert res_bad_date.status_code == 400
    assert "必须早于终止日期" in res_bad_date.json()["detail"]

    # 2. 投资建议敏感词拦截
    res_advice = client.post("/api/verify", json={
        "ticker": "300308.SZ",
        "ticker_name": "中际旭创",
        "hypothesis": "强烈建议全仓买入，目标价200元",
        "analysis_start_date": "2023-01-01",
        "analysis_end_date": "2024-12-31"
    })
    assert res_advice.status_code == 400
    assert "包含违规投资建议敏感词" in res_advice.json()["detail"]

    # 3. 空命题拦截
    res_empty = client.post("/api/verify", json={
        "ticker": "300308.SZ",
        "ticker_name": "中际旭创",
        "hypothesis": "",
        "analysis_start_date": "2023-01-01",
        "analysis_end_date": "2024-12-31"
    })
    assert res_empty.status_code == 400
    assert "提交的验证命题陈述不能为空" in res_empty.json()["detail"]

def test_api_verify_and_task_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr("fin_verifier.core.state_manager.TASKS_DATA_DIR", tmp_path)
    # Monkeypatch pipeline to not execute live network background thread in offline tests
    monkeypatch.setattr(fin_verifier.main.pipeline, "run_pipeline", lambda task_id: None)

    # 1. 验证启动接口
    payload = {
        "ticker": "300308.SZ",
        "ticker_name": "中际旭创",
        "hypothesis": "算力需求拉动中际旭创高端光模块结构升级",
        "clarified_hypothesis": "算力需求拉动中际旭创高端光模块放量提升盈利能力",
        "analysis_start_date": "2023-01-01",
        "analysis_end_date": "2024-12-31"
    }
    res = client.post("/api/verify", json=payload)
    assert res.status_code == 200
    data = res.json()
    task_id = data["task_id"]
    assert task_id.startswith("task-")
    assert data["status"] == "CREATED"

    # 2. 查询任务详情
    res_detail = client.get(f"/api/tasks/{task_id}")
    assert res_detail.status_code == 200
    detail = res_detail.json()
    assert detail["task_id"] == task_id
    assert detail["ticker"] == "300308.SZ"
    assert detail["ticker_name"] == "中际旭创"

    # 3. 收藏与标签修改 (PATCH /api/tasks/{task_id}/star)
    star_res = client.patch(
        f"/api/tasks/{task_id}/star",
        json={"starred": True, "tags": ["算力龙头", "算力龙头", "光模块"]}
    )
    assert star_res.status_code == 200
    star_data = star_res.json()
    assert star_data["starred"] is True
    assert star_data["tags"] == ["算力龙头", "光模块"]  # 验证标签去重

    # 4. 任务列表接口 (GET /api/tasks)
    list_res = client.get("/api/tasks?starred_only=true")
    assert list_res.status_code == 200
    tasks_list = list_res.json()
    assert any(t["task_id"] == task_id for t in tasks_list)

    # 5. 测试追问接口（当任务尚未完成报告时，应返回明确契约错误）
    inq_res = client.post(f"/api/tasks/{task_id}/inquire", json={"question": "中际旭创800G收入多少？"})
    assert inq_res.status_code == 400
    assert "尚未完成研报合成" in inq_res.json()["detail"]

def test_api_compare(tmp_path, monkeypatch):
    monkeypatch.setattr("fin_verifier.core.state_manager.TASKS_DATA_DIR", tmp_path)

    # 创建两个 mock 落盘任务用于测试对比
    t_a = ResearchTask(
        task_id="t-compare-A",
        status=TaskStatus.COMPLETED,
        ticker="300308.SZ",
        ticker_name="中际旭创",
        original_hypothesis="旭创命题",
        clarified_hypothesis="旭创澄清命题",
        analysis_start_date="2023-01-01",
        analysis_end_date="2024-12-31",
        data_cutoff_time="2025-01-01",
        latest_financial_period="2024-Q3",
        created_at="2026-10-30T10:00:00",
        updated_at="2026-10-30T10:00:00"
    )
    t_b = ResearchTask(
        task_id="t-compare-B",
        status=TaskStatus.COMPLETED,
        ticker="300750.SZ",
        ticker_name="宁德时代",
        original_hypothesis="宁德命题",
        clarified_hypothesis="宁德澄清命题",
        analysis_start_date="2023-01-01",
        analysis_end_date="2024-12-31",
        data_cutoff_time="2025-01-01",
        latest_financial_period="2024-Q3",
        created_at="2026-10-30T10:00:00",
        updated_at="2026-10-30T10:00:00"
    )
    TaskStateManager.save_task(t_a)
    TaskStateManager.save_task(t_b)

    res = client.get("/api/compare?task_a=t-compare-A&task_b=t-compare-B")
    assert res.status_code == 200
    comp_data = res.json()
    assert comp_data["comparison_summary"]["task_a_ticker"] == "中际旭创 (300308.SZ)"
    assert comp_data["comparison_summary"]["task_b_ticker"] == "宁德时代 (300750.SZ)"
    assert comp_data["comparison_summary"]["is_same_ticker"] is False
