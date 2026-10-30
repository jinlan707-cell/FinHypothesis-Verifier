import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from fin_verifier.core.config import PROJECT_ROOT, FIN_VERIFIER_DIR
from fin_verifier.core.models import (
    ResearchTask,
    TaskStatus,
    DemoItem,
    TickerItem,
    ClarifyRequest,
    ClarifyResponse,
    VerifyRequest,
    VerifyResponse,
    TaskSummaryItem,
    StarTaskRequest,
    CompareResponse,
    InquireRequest,
    InquireResponse
)
from fin_verifier.core.state_manager import TaskStateManager
from fin_verifier.core.pipeline import VerificationPipeline
from fin_verifier.core.validator import PipelineValidator
from fin_verifier.core.inquiry_engine import InquiryEngine
from fin_verifier.tools.fuyao_client import FuyaoClient
from fin_verifier.tools.llm_client import LLMClient
from fin_verifier.prompts.clarifier_prompt import CLARIFIER_SYSTEM_PROMPT, ClarifyOutput

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("fin_verifier.main")

app = FastAPI(
    title="FinHypothesis-Verifier API",
    description="投资命题与多证据验证系统后端核心接口（9个REST API）",
    version="1.0.0"
)

@app.on_event("startup")
def on_startup():
    """服务启动时收敛所有孤儿非终态任务"""
    cleaned = TaskStateManager.cleanup_orphan_tasks()
    if cleaned > 0:
        logger.info(f"系统启动完成，已收敛清理 {cleaned} 个异常中断僵尸任务至 FAILED 状态")
    else:
        logger.info("系统启动完成，任务存储库状态健康")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

fuyao_client = FuyaoClient()
llm_client = LLMClient()
pipeline = VerificationPipeline(fuyao_client=fuyao_client, llm_client=llm_client)
inquiry_engine = InquiryEngine(llm_client=llm_client)

WEB_DIR = FIN_VERIFIER_DIR / "web"
WEB_DIR.mkdir(parents=True, exist_ok=True)


# ==============================================================================
# 9 个核心 REST 接口实现
# ==============================================================================

# 1. GET /api/demos
@app.get("/api/demos", response_model=List[DemoItem], summary="载入典型命题模板（不含硬编码答案）")
def get_demos():
    return [
        DemoItem(
            id="demo-zhongji",
            ticker="300308.SZ",
            ticker_name="中际旭创",
            title="中际旭创盈利能力跃升归因：高端光模块结构升级",
            hypothesis="算力需求拉动下，中际旭创盈利能力大幅跃升主要得益于高端光模块产品结构升级",
            analysis_start_date="2023-01-01",
            analysis_end_date="2024-12-31",
            description="针对AI算力光模块龙头中际旭创，验证其营业收入与净利润大幅跃升的核心驱动因素，核对800G/1.6T等高端产品结构升级事实，审慎检验毛利率变化及现金流质量。"
        ),
        DemoItem(
            id="demo-catl",
            ticker="300750.SZ",
            ticker_name="宁德时代",
            title="宁德时代动力电池盈利韧性与海外业务拓展",
            hypothesis="国内动力电池价格竞争加剧背景下，宁德时代整体盈利韧性主要得益于海外高毛利业务拓展",
            analysis_start_date="2023-01-01",
            analysis_end_date="2024-12-31",
            description="核验动力电池龙头境内外收入结构与毛利率变动，考察行业价格战背景下海外市场对利润中枢的支撑作用。"
        ),
        DemoItem(
            id="demo-naura",
            ticker="002371.SZ",
            ticker_name="北方华创",
            title="北方华创规模效应显现与主营盈利质量持续提升",
            hypothesis="半导体设备国产替代推进下，北方华创规模效应逐步显现并带动主营盈利质量持续提升",
            analysis_start_date="2023-01-01",
            analysis_end_date="2024-12-31",
            description="核验半导体设备龙头业务规模扩张与毛利率变动，考察规模效应带动主营盈利质量提升的持续性。"
        )
    ]


# 2. GET /api/tickers/search
@app.get("/api/tickers/search", response_model=List[TickerItem], summary="标的查询与消歧候选")
def search_tickers(q: str = Query(..., min_length=1, description="查询关键词，如 '中际旭创' 或 '300308'")):
    try:
        raw_items = fuyao_client.search_ticker(q)
        results: List[TickerItem] = []
        for item in raw_items:
            thscode = item.get("thscode") or item.get("code") or ""
            name = item.get("name") or item.get("secName") or ""
            market = item.get("market") or ""
            if thscode and name:
                results.append(TickerItem(thscode=thscode, name=name, market=market))
        return results
    except Exception as e:
        logger.error(f"标的检索异常: {e}")
        # 严禁使用硬编码 Mock 假数据兜底，透传真实可诊断错误
        raise HTTPException(status_code=502, detail=f"上游标的检索服务异常: {e}")


# 3. POST /api/clarify
@app.post("/api/clarify", response_model=ClarifyResponse, summary="Step 0 命题澄清与合规改写")
def clarify_hypothesis(req: ClarifyRequest):
    # 标的消歧
    ticker_name = req.ticker
    try:
        candidates = fuyao_client.search_ticker(req.ticker)
        if candidates:
            first_c = candidates[0]
            req.ticker = first_c.get("thscode") or req.ticker
            ticker_name = first_c.get("name") or req.ticker
    except Exception as e:
        logger.warning(f"澄清前标的消歧检索失败: {e}")

    user_prompt = f"""标的: {req.ticker} {ticker_name}
原始命题: {req.hypothesis}
建议起始日期: {req.analysis_start_date or '2023-01-01'}
建议终止日期: {req.analysis_end_date or '2024-12-31'}

请给出合规、严谨的澄清命题与研究边界。"""

    try:
        res: ClarifyOutput = llm_client.call_structured(
            system_prompt=CLARIFIER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=ClarifyOutput
        )
        return ClarifyResponse(
            ticker=req.ticker,
            ticker_name=ticker_name,
            clarified_hypothesis=res.clarified_hypothesis,
            analysis_start_date=res.analysis_start_date or req.analysis_start_date or "2023-01-01",
            analysis_end_date=res.analysis_end_date or req.analysis_end_date or "2024-12-31",
            compliance_notes=res.compliance_notes,
            suggested_dimensions=res.suggested_dimensions
        )
    except Exception as e:
        logger.error(f"命题澄清异常: {e}")
        # 严禁伪造硬编码假澄清文案，真实透传异常
        raise HTTPException(status_code=502, detail=f"命题澄清与合规改写服务异常: {e}")


# 4. POST /api/verify
@app.post("/api/verify", response_model=VerifyResponse, summary="创建任务并异步启动核验流水线")
def verify_hypothesis(req: VerifyRequest):
    # 1. 严格二次校验起止日期 (start_date < end_date)
    start_date = req.analysis_start_date or "2023-01-01"
    end_date = req.analysis_end_date or "2024-12-31"
    if start_date >= end_date:
        raise HTTPException(
            status_code=400,
            detail=f"分析起始日期 ({start_date}) 必须早于终止日期 ({end_date})"
        )

    # 2. 校验命题内容非空与合规词
    hypothesis_text = req.clarified_hypothesis or req.hypothesis or req.original_hypothesis or ""
    if not hypothesis_text.strip():
        raise HTTPException(status_code=400, detail="提交的验证命题陈述不能为空")

    for bad_word in PipelineValidator.COMPLIANCE_FORBIDDEN_WORDS:
        if bad_word in hypothesis_text:
            raise HTTPException(
                status_code=400,
                detail=f"提交命题包含违规投资建议敏感词 '{bad_word}'，请先在 Step 0 澄清并消除买卖导向"
            )

    # 3. 标的确认状态校验与名称解析
    ticker_name = req.ticker_name or req.ticker
    try:
        candidates = fuyao_client.search_ticker(req.ticker)
        if candidates:
            # 优先精确匹配 thscode
            matched = next((c for c in candidates if c.get("thscode") == req.ticker), candidates[0])
            req.ticker = matched.get("thscode") or req.ticker
            ticker_name = matched.get("name") or ticker_name
    except Exception as e:
        logger.warning(f"verify 阶段标的复核警告: {e}")

    task_id = f"task-{datetime.now().strftime('%Y%m%d%H%M%S')}-{int(datetime.now().timestamp() * 1000) % 1000}"
    now_str = datetime.now().isoformat()

    # 4. 初始落盘：尚未真实拉取数据前 latest_financial_period 必须为空字符串 ""
    task = ResearchTask(
        task_id=task_id,
        status=TaskStatus.CREATED,
        starred=False,
        tags=[ticker_name],
        ticker=req.ticker,
        ticker_name=ticker_name,
        original_hypothesis=req.original_hypothesis or req.hypothesis or hypothesis_text,
        clarified_hypothesis=hypothesis_text,
        analysis_start_date=start_date,
        analysis_end_date=end_date,
        data_cutoff_time=datetime.now().strftime("%Y-%m-%d"),
        latest_financial_period="",
        created_at=now_str,
        updated_at=now_str
    )

    # 立即原子落盘
    TaskStateManager.save_task(task)

    # 放入后台独立线程执行，不阻塞事件循环，保证即时响应
    worker_thread = threading.Thread(
        target=pipeline.run_pipeline,
        args=(task_id,),
        daemon=True,
        name=f"worker-{task_id}"
    )
    worker_thread.start()

    logger.info(f"验证任务已异步派发: {task_id} ({task.ticker} {task.ticker_name})")
    return VerifyResponse(
        task_id=task_id,
        status=TaskStatus.CREATED,
        message="任务已成功创建并异步启动核验流水线"
    )


# 5. GET /api/tasks/{task_id}
@app.get("/api/tasks/{task_id}", response_model=ResearchTask, summary="查询任务实时状态、全量事实与研报")
def get_task_detail(task_id: str):
    task = TaskStateManager.load_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"未找到研究任务: {task_id}")
    return task


# 6. GET /api/tasks
@app.get("/api/tasks", response_model=List[TaskSummaryItem], summary="历史任务列表与收藏筛选")
def list_tasks(starred_only: bool = Query(False, description="是否仅展示已收藏任务")):
    return TaskStateManager.list_tasks(starred_only=starred_only)


# 7. PATCH /api/tasks/{task_id}/star
@app.patch("/api/tasks/{task_id}/star", response_model=ResearchTask, summary="设置任务收藏状态与自定义标签")
def update_task_star(task_id: str, req: StarTaskRequest):
    updated = TaskStateManager.set_star_and_tags(task_id, starred=req.starred, tags=req.tags)
    if not updated:
        raise HTTPException(status_code=404, detail=f"未找到研究任务: {task_id}")
    return updated


# 8. GET /api/compare
@app.get("/api/compare", response_model=CompareResponse, summary="双任务并排对比视图（基于已存数据，不调LLM）")
def compare_tasks(
    task_a: str = Query(..., description="任务 A 的 ID"),
    task_b: str = Query(..., description="任务 B 的 ID")
):
    t_a = TaskStateManager.load_task(task_a)
    t_b = TaskStateManager.load_task(task_b)

    if not t_a:
        raise HTTPException(status_code=404, detail=f"未找到任务 A: {task_a}")
    if not t_b:
        raise HTTPException(status_code=404, detail=f"未找到任务 B: {task_b}")

    summary = {
        "task_a_id": t_a.task_id,
        "task_a_ticker": f"{t_a.ticker_name} ({t_a.ticker})",
        "task_a_verdict": t_a.report.verdict_status.value if t_a.report else "未完成",
        "task_a_confidence": t_a.report.confidence_level.value if t_a.report else "未知",
        "task_a_evidences_count": len(t_a.evidence_items),
        
        "task_b_id": t_b.task_id,
        "task_b_ticker": f"{t_b.ticker_name} ({t_b.ticker})",
        "task_b_verdict": t_b.report.verdict_status.value if t_b.report else "未完成",
        "task_b_confidence": t_b.report.confidence_level.value if t_b.report else "未知",
        "task_b_evidences_count": len(t_b.evidence_items),

        "is_same_ticker": t_a.ticker == t_b.ticker,
        "comparison_note": "对比结果由已存结构化数据确定性拼装，未引入额外模型不确定性。"
    }

    return CompareResponse(
        task_a=t_a,
        task_b=t_b,
        comparison_summary=summary
    )


# 9. POST /api/tasks/{task_id}/inquire
@app.post("/api/tasks/{task_id}/inquire", response_model=InquireResponse, summary="基于已存事实回答追问并持久化")
def inquire_task(task_id: str, req: InquireRequest):
    if not req.question or not req.question.strip():
        raise HTTPException(status_code=400, detail="追问内容不能为空")
    
    try:
        resp = inquiry_engine.answer_inquiry(task_id, req.question.strip())
        return resp
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"处理追问异常: {e}")
        raise HTTPException(status_code=500, detail=f"处理追问失败: {e}")


# 挂载 Web 前端静态文件（放在所有 API 路由之后）
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
