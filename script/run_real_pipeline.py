import sys
import os
import json
import logging
from datetime import datetime
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fin_verifier.core.models import ResearchTask, TaskStatus
from fin_verifier.core.state_manager import TaskStateManager
from fin_verifier.core.pipeline import VerificationPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("run_real_pipeline")

def main():
    ticker = "300308.SZ"
    ticker_name = "中际旭创"
    original_hypo = "算力需求拉动下，中际旭创盈利能力大幅跃升主要得益于高端光模块产品结构升级"
    clarified_hypo = "算力需求拉动下，中际旭创盈利能力大幅跃升主要得益于高端光模块产品结构升级"
    start_date = "2023-01-01"
    end_date = "2024-12-31"

    task_id = f"task-real-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    now_str = datetime.now().isoformat()

    task = ResearchTask(
        task_id=task_id,
        status=TaskStatus.CREATED,
        starred=True,
        tags=["中际旭创", "光模块", "算力龙头"],
        ticker=ticker,
        ticker_name=ticker_name,
        original_hypothesis=original_hypo,
        clarified_hypothesis=clarified_hypo,
        analysis_start_date=start_date,
        analysis_end_date=end_date,
        data_cutoff_time=datetime.now().strftime("%Y-%m-%d"),
        latest_financial_period="",
        created_at=now_str,
        updated_at=now_str
    )
    TaskStateManager.save_task(task)
    logger.info(f"创建真实核验任务: {task_id}")

    pipeline = VerificationPipeline()
    res_task = pipeline.run_pipeline(task_id)

    print("\n" + "="*80)
    print(f"任务执行结束: ID={res_task.task_id}, 终态 Status={res_task.status}")
    print(f"最新已披露报告期 latest_financial_period={res_task.latest_financial_period}")
    print(f"取数报告期 retrieved_periods={res_task.retrieved_periods}")
    print(f"原始材料数量 raw_records count={len(res_task.raw_records)}")
    print(f"核验事实数量 extracted_facts count={len(res_task.extracted_facts)}")
    print(f"确定性计算数量 computed_metrics count={len(res_task.computed_metrics)}")
    print(f"证据卡片数量 evidence_items count={len(res_task.evidence_items)}")

    print("\n【子假设判定与证据缺口回写核验】:")
    for sh in res_task.sub_hypotheses:
        print(f"  - [{sh.id}] [{sh.dimension}] {sh.statement}")
        print(f"    Verdict: {sh.verdict_status.value} | Confidence: {sh.confidence_level.value}")
        print(f"    Supported EVIDs: {sh.supported_evidence_ids}")
        print(f"    Against EVIDs: {sh.against_evidence_ids}")
        print(f"    Gap Note: {sh.evidence_gap_note}")

    if res_task.report:
        print("\n【最终研报】:")
        print(f"  - Verdict Headline: {res_task.report.verdict_headline}")
        print(f"  - Overall Verdict: {res_task.report.verdict_status.value} (Confidence: {res_task.report.confidence_level.value})")
        print(f"  - Cited Evidences: {res_task.report.cited_evidence_ids}")
        print(f"  - Facts Summary ({len(res_task.report.facts_summary)} 项): {res_task.report.facts_summary[:2]}")
        print(f"  - Inferences Summary: {res_task.report.inferences_summary}")
        print(f"  - Uncertainties / Gaps: {res_task.report.uncertainties_summary}")
        print(f"  - Conflict Resolution: {res_task.report.conflict_resolution}")
        print(f"  - Falsification Triggers ({len(res_task.report.falsification_triggers)} 项):")
        for trig in res_task.report.falsification_triggers:
            print(f"      * [{trig.trigger_type.value}] {trig.condition_description} (指标: {trig.observation_metric}, 依据: {trig.analytical_basis})")
    else:
        print(f"\n【错误原因】: {res_task.error_message}")

    # 落盘文件路径
    task_file = Path("fin_verifier/data/tasks") / f"{task_id}.json"
    print(f"\n任务已落盘至: {task_file.resolve()}")
    assert task_file.exists(), "任务落盘文件不存在！"

if __name__ == "__main__":
    main()
