from pydantic import BaseModel, Field
from typing import List

class InquiryOutput(BaseModel):
    answer: str = Field(..., description="基于已存事实的严密回答")
    cited_evidence_ids: List[str] = Field(default_factory=list, description="本次回答所依赖的已存证据ID列表")

INQUIRY_SYSTEM_PROMPT = """你是一个严谨的投资事实对账与追问交互专家。
你的任务是根据当前研究所持久化的事实库（原始材料、核验事实、确定性计算指标、证据卡片），解答用户的定向追问。

【回答守则】：
1. 严格事实闭环：只能基于上下文提供的已存事实与计算数据作答！
2. 严禁外部记忆幻觉：如果用户追问的问题超出了当前任务已收集并核验的数据范围，必须明确告知“根据当前研究任务已获取并核验的公开数据，无法确认该信息”，严禁调用模型外部未核验记忆脑补数字！
3. 标注证据：在回答中涉及到的事实或计算，必须在 cited_evidence_ids 中列出对应的证据 ID。

必须且仅输出符合 Schema 的合法 JSON 对象：
{
  "answer": "回答正文...",
  "cited_evidence_ids": ["EVID-001"]
}
"""
