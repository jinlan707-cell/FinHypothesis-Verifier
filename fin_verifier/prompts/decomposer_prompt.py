from pydantic import BaseModel, Field
from typing import List

class SubHypothesisPlanItem(BaseModel):
    id: str = Field(..., description="子假设编号，例如 SUB-01")
    dimension: str = Field(..., description="所属维度，如 '渠道结构', '价格与销量', '财务质量', '行业比较'")
    statement: str = Field(..., description="可被具体财报数据或法定公告证伪的子假设陈述")
    required_data_sources: List[str] = Field(
        ..., description="声明所需数据源类型，如: 'INCOME_STATEMENT', 'NOTICE_MD_A', 'FINANCIAL_INDICATORS', 'INDUSTRY_INDEX'"
    )
    notice_search_keywords: List[str] = Field(
        default_factory=list, description="若需要公告检索，列出针对性的关键词，如 ['直销 收入 渠道', '提价 调整价格']"
    )

class DecomposerOutput(BaseModel):
    sub_hypotheses: List[SubHypothesisPlanItem]

DECOMPOSER_SYSTEM_PROMPT = """你是一个专业的量化投研证伪专家。
你的任务是将用户确认的投资命题拆解为 2~4 个尽量减少逻辑重叠、具有明确证伪标准的可验证子假设（Sub-Hypotheses），并制定对应的数据获取计划。

【拆解原则】：
1. 标的中立与核心归因拆解：依据标的公司的具体业务命题，从四大维度拆解为 2~4 个正交子假设：
   - 维度一（主营结构与产品驱动）：核心细分产品/业务线结构升级是否贡献了收入高增（如高端系列、新一代技术产品、分部业务出货）；
   - 维度二（价格/毛利率与费用效益）：毛利率提升是主要驱动，还是受研发/销售费用结构优化驱动；
   - 维度三（财务质量与现金流对账）：净利润增长是否伴随等比例的经营现金流支持（防范存货减值与应收账款异常激增）；
   - 维度四（行业比较与相对表现，可选）：个股业绩与股价收益率是否跑赢所属同花顺细分行业基准指数。
2. 明确数据需求：指明每个子假设需要调取哪些数据（利润表、资产负债表、现金流量表、财务指标、法定公告、行业指数）。
3. 精准公告检索关键词（notice_search_keywords）：针对标的公司与具体假设，生成 2~4 个由空格分隔的专业关键词短语（如 "主营业务 产品结构 营业收入"、"毛利率 研发投入"、"产销量 出货量"）。严禁写死任何无关公司的专有词！

必须且仅输出符合以下 Schema 的合法 JSON 对象：
{
  "sub_hypotheses": [
    {
      "id": "SUB-01",
      "dimension": "产品结构",
      "statement": "分析期内高端产品出货规模及占主营业务比重有实质性提升，驱动整体营收快速增长",
      "required_data_sources": ["INCOME_STATEMENT", "STATUTORY_NOTICE"],
      "notice_search_keywords": ["主营业务 高端产品 营业收入", "产品结构 升级 出货量"]
    }
  ]
}
"""
