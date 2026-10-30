import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from fin_verifier.core.models import ResearchTask, InquireResponse
from fin_verifier.core.state_manager import TaskStateManager
from fin_verifier.tools.llm_client import LLMClient
from fin_verifier.prompts.inquiry_prompt import INQUIRY_SYSTEM_PROMPT, InquiryOutput

logger = logging.getLogger("fin_verifier.core.inquiry_engine")

class InquiryEngine:
    """
    基于已存事实库的追问交互引擎。
    严格基于落盘任务的原始材料、抽取事实、计算指标与证据卡片回答，
    严禁调用未核验的大模型外部记忆幻觉编造数据。
    """

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or LLMClient()

    def answer_inquiry(self, task_id: str, question: str) -> InquireResponse:
        task = TaskStateManager.load_task(task_id)
        if not task:
            raise ValueError(f"未找到任务: {task_id}")

        if not task.report:
            raise ValueError(f"任务 [{task_id}] 尚未完成研报合成，无法针对结论进行事实追问")

        # 组织上下文上下文凭证库
        facts_summary = [
            f"事实[{f.fact_id}]: {f.period} {f.metric_name} = {f.numeric_value} {f.unit} (原话: \"{f.exact_quote}\")"
            for f in task.extracted_facts
        ]
        comps_summary = [
            f"计算[{c.computation_id}]: {c.name} = {c.value}{c.unit} (计算路径: {c.trace})"
            for c in task.computed_metrics
        ]
        evids_summary = [
            f"证据[{e.evidence_id}]: {e.title} -> {e.summary} (极性: {e.polarity.value})"
            for e in task.evidence_items
        ]

        context_payload = f"""【标的信息】: {task.ticker_name} ({task.ticker})
【主命题】: {task.clarified_hypothesis}
【研报结论】: {task.report.verdict_headline} (判定: {task.report.verdict_status.value}, 置信度: {task.report.confidence_level.value})

【已核验客观事实库】:
{chr(10).join(facts_summary) if facts_summary else '无'}

【已执行确定性计算指标】:
{chr(10).join(comps_summary) if comps_summary else '无'}

【归纳证据卡片】:
{chr(10).join(evids_summary) if evids_summary else '无'}
"""

        user_prompt = f"""{context_payload}

【用户追问】:
{question}

请严格基于上述提供的事实、计算与证据卡片回答。若上述信息未包含答案所需数据，请直接明确说明“根据当前任务已获取并核验的公开数据，无法确认该信息”，严禁幻觉。
"""
        try:
            res: InquiryOutput = self.llm.call_structured(
                system_prompt=INQUIRY_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_model=InquiryOutput
            )

            # 校验引用的证据 ID 是否合法
            valid_eids = {e.evidence_id for e in task.evidence_items}
            clean_cites = [eid for eid in res.cited_evidence_ids if eid in valid_eids]

            inquiry_record = {
                "question": question,
                "answer": res.answer,
                "cited_evidence_ids": clean_cites,
                "created_at": datetime.now().isoformat()
            }

            # 追加持久化至任务实体
            task.inquiries.append(inquiry_record)
            TaskStateManager.save_task(task)

            return InquireResponse(
                question=question,
                answer=res.answer,
                cited_evidence_ids=clean_cites,
                created_at=inquiry_record["created_at"]
            )
        except Exception as e:
            logger.error(f"追问处理失败: {e}")
            raise
