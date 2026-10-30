import os
import sys
import json
import time
from datetime import datetime
from pathlib import Path

# 确保路径解析基于文件模块位置
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fin_verifier.core.models import ResearchTask, TaskStatus
from fin_verifier.core.state_manager import TaskStateManager
from fin_verifier.core.pipeline import VerificationPipeline
from fin_verifier.core.inquiry_engine import InquiryEngine

def main():
    print("=" * 70)
    print("【贵州茅台 600519.SH 真实端到端全链路核验】")
    print("=" * 70)

    task_id = f"task-maotai-{int(time.time())}"
    now_str = datetime.now().isoformat()

    # 创建初始任务
    task = ResearchTask(
        task_id=task_id,
        status=TaskStatus.CREATED,
        starred=True,
        tags=["贵州茅台", "渠道驱动", "提价验证"],
        ticker="600519.SH",
        ticker_name="贵州茅台",
        original_hypothesis="茅台这几年利润增长这么稳，感觉基本靠提价和直销，跟总销量关系不大吧？",
        clarified_hypothesis="在目标分析期内，贵州茅台营业收入与归母净利润的增长是否主要与直销渠道占比提升及产品提价解释一致，且销量扩张的相对贡献弱于价格与渠道贡献。",
        analysis_start_date="2023-01-01",
        analysis_end_date="2024-12-31",
        data_cutoff_time="2025-01-01",
        latest_financial_period="2024-Q3",
        retrieved_periods=[],
        created_at=now_str,
        updated_at=now_str
    )

    TaskStateManager.save_task(task)
    print(f"1. 任务创建成功: {task_id}")

    # 启动流水线
    pipeline = VerificationPipeline()
    t0 = time.time()
    completed_task = pipeline.run_pipeline(task_id)
    duration = time.time() - t0

    print(f"\n2. 流水线执行结束，耗时: {duration:.2f}s，最终状态: {completed_task.status.value}")
    if completed_task.error_message:
        print(f"   错误信息: {completed_task.error_message}")

    print("\n3. 核验产物统计:")
    print(f"   - 原始真实材料条数: {len(completed_task.raw_records)}")
    print(f"   - 提取并核验的事实条数: {len(completed_task.extracted_facts)}")
    print(f"   - 确定性计算项条数: {len(completed_task.computed_metrics)}")
    print(f"   - 子假设条数: {len(completed_task.sub_hypotheses)}")
    print(f"   - 归类证据卡片条数: {len(completed_task.evidence_items)}")

    if completed_task.report:
        rep = completed_task.report
        print("\n4. 结构化研报核心输出:")
        print(f"   【判定结论】: {rep.verdict_status.value} (置信度: {rep.confidence_level.value})")
        print(f"   【结论标题】: {rep.verdict_headline}")
        print("   【核心事实摘要】:")
        for f in rep.facts_summary[:3]:
            print(f"     * {f}")
        print("   【审慎逻辑推论】:")
        for inf in rep.inferences_summary[:2]:
            print(f"     * {inf}")
        print("   【客观不确定性与缺口】:")
        for unc in rep.uncertainties_summary[:2]:
            print(f"     * {unc}")
        print("   【反转触发条件 (区分历史与未来)】:")
        for trig in rep.falsification_triggers:
            print(f"     * [{trig.trigger_type.value}]: {trig.condition_description} (依据: {trig.analytical_basis})")
        print(f"   【引用证据 ID】: {rep.cited_evidence_ids}")

        # 5. 测试追问引擎
        print("\n5. 测试基于已存事实的追问交互:")
        inquiry_engine = InquiryEngine()
        q = "请问根据2023年年报已核验的事实，茅台营业收入规模是多少？有没有关于直销和提价的数据？"
        inq_resp = inquiry_engine.answer_inquiry(task_id, q)
        print(f"   追问问题: {inq_resp.question}")
        print(f"   追问回答: {inq_resp.answer}")
        print(f"   引用证据: {inq_resp.cited_evidence_ids}")

    task_file = TaskStateManager.get_task_file(task_id)
    print(f"\n6. 任务持久化文件路径: {task_file} (存在: {task_file.exists()})")
    print("=" * 70)

if __name__ == "__main__":
    main()
