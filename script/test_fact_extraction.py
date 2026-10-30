import os
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fin_verifier.tools.llm_client import LLMClient
from fin_verifier.prompts.fact_extractor_prompt import FACT_EXTRACTOR_SYSTEM_PROMPT, FactExtractorOutput

llm = LLMClient()
sample_text = """
【材料 ID: RAW-NOTI-006 | 标题: 贵州茅台2023年年度报告】
报告期内，公司实现营业总收入1,505.60亿元，同比增长18.04%；归属于上市公司股东的净利润747.34亿元，同比增长19.16%。

【材料 ID: RAW-NOTI-007 | 标题: 贵州茅台重大事项公告】
经研究决定，自2023年11月1日起上调本公司53%vol贵州茅台酒（飞天、五星）出厂价格，平均上调幅度约为20％。
"""

user_prompt = f"""标的: 贵州茅台
请从以下给定的材料中，提取 2~3 项最核心的原子事实。
【绝对铁律】：每个事实的 exact_quote 必须是一字不差从对应材料原文中摘录的原句，严禁修改任何标点符号和小数点！

材料内容：
{sample_text}
"""

print("开始调用 LLM 测试事实抽取...")
t0 = time.time()
res = llm.call_structured(
    system_prompt=FACT_EXTRACTOR_SYSTEM_PROMPT,
    user_prompt=user_prompt,
    response_model=FactExtractorOutput,
    max_tokens=800
)
print(f"调用完成，耗时: {time.time() - t0:.2f}s")
for f in res.extracted_facts:
    print(f"  [{f.fact_id}] ({f.raw_ref_id}) {f.metric_name} = {f.numeric_value} {f.unit} | Quote: '{f.exact_quote}'")
