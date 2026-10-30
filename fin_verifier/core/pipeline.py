import logging
import re
import traceback
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

from fin_verifier.core.models import (
    ResearchTask,
    TaskStatus,
    VerdictStatus,
    ConfidenceLevel,
    SourceType,
    RawRefRecord,
    ExtractedFact,
    ComputedMetric,
    EvidenceItem,
    SubHypothesis,
    FinalReport,
    Polarity
)
from fin_verifier.core.state_manager import TaskStateManager
from fin_verifier.core.validator import PipelineValidator
from fin_verifier.tools.fuyao_client import FuyaoClient, INDEX_ID_CN_MAP
from fin_verifier.tools.ifind_client import IFinDClient
from fin_verifier.tools.llm_client import LLMClient
from fin_verifier.tools.calculator import DeterministicCalculator
from fin_verifier.tools.unit_normalizer import UnitNormalizer

from fin_verifier.prompts.decomposer_prompt import DECOMPOSER_SYSTEM_PROMPT, DecomposerOutput
from fin_verifier.prompts.fact_extractor_prompt import FACT_EXTRACTOR_SYSTEM_PROMPT, FactExtractorOutput
from fin_verifier.prompts.sufficiency_prompt import SUFFICIENCY_SYSTEM_PROMPT, SufficiencyOutput
from fin_verifier.prompts.classifier_prompt import CLASSIFIER_SYSTEM_PROMPT, ClassifierOutput
from fin_verifier.prompts.synthesizer_prompt import SYNTHESIZER_SYSTEM_PROMPT, SynthesizerOutput

logger = logging.getLogger("fin_verifier.core.pipeline")


class VerificationPipeline:
    """
    投资命题全流程核验编排流水线（标的中立通用版本）。
    严格执行 Step 0 ~ Step 6 状态机顺序流转，
    基于真实数据驱动、两阶段质检门禁拦截伪造。
    """

    def __init__(
        self,
        fuyao_client: Optional[FuyaoClient] = None,
        ifind_client: Optional[IFinDClient] = None,
        llm_client: Optional[LLMClient] = None
    ):
        self.fuyao = fuyao_client or FuyaoClient()
        self.ifind = ifind_client or IFinDClient()
        self.llm = llm_client or LLMClient()

    def run_pipeline(self, task_id: str) -> ResearchTask:
        """运行完整核验流水线"""
        task = TaskStateManager.load_task(task_id)
        if not task:
            raise ValueError(f"未找到任务: {task_id}")

        try:
            logger.info(f"=== 开始执行任务流水线: {task_id} ({task.ticker} {task.ticker_name}) ===")

            # ------------------------------------------------------------------
            # Step 1: Decomposing (子假设拆解与取数计划制定)
            # ------------------------------------------------------------------
            task.status = TaskStatus.DECOMPOSING
            TaskStateManager.save_task(task)
            self._step_decompose(task)

            # ------------------------------------------------------------------
            # Step 2: Retrieving (获取真实金融数据与原始材料)
            # ------------------------------------------------------------------
            task.status = TaskStatus.RETRIEVING
            TaskStateManager.save_task(task)
            self._step_retrieve(task)

            # ------------------------------------------------------------------
            # Step 3: Extracting & Computing (事实抽取与确定性计算)
            # ------------------------------------------------------------------
            task.status = TaskStatus.EXTRACTING_AND_COMPUTING
            TaskStateManager.save_task(task)
            self._step_extract_and_compute(task)

            # ------------------------------------------------------------------
            # Step 4: Sufficiency Checking & Replenishing (覆盖度核对与最多1轮补检)
            # ------------------------------------------------------------------
            task.status = TaskStatus.SUFFICIENCY_CHECKING
            TaskStateManager.save_task(task)
            needs_replenish, replenish_queries = self._step_check_sufficiency(task)

            if needs_replenish and replenish_queries and task.meta.get("replenish_count", 0) == 0:
                task.status = TaskStatus.REPLENISHING
                task.meta["replenish_count"] = 1
                TaskStateManager.save_task(task)
                self._step_replenish(task, replenish_queries)
                
                # 补检获取的新材料状态先切回 EXTRACTING_AND_COMPUTING 重新流转
                task.status = TaskStatus.EXTRACTING_AND_COMPUTING
                TaskStateManager.save_task(task)
                self._step_extract_and_compute(task, is_replenish=True)

            # ------------------------------------------------------------------
            # Step 5: Classifying (证据归类与审慎归因)
            # ------------------------------------------------------------------
            task.status = TaskStatus.CLASSIFYING
            TaskStateManager.save_task(task)
            self._step_classify(task)

            # ------------------------------------------------------------------
            # Step 6: Validating & Synthesizing (两阶段质检与报告合成)
            # ------------------------------------------------------------------
            task.status = TaskStatus.VALIDATING
            TaskStateManager.save_task(task)

            # 6.1 合成前门禁质检 (纯代码硬核验)
            pre_val = PipelineValidator.validate_before_synthesis(
                raw_records=task.raw_records,
                facts=task.extracted_facts,
                computed_metrics=task.computed_metrics,
                evidence_items=task.evidence_items
            )
            if not pre_val.is_valid:
                logger.error(f"合成前门禁核验失败: {pre_val.issues}")
                task.status = TaskStatus.FAILED
                task.error_message = f"合成前门禁校验未通过: {'; '.join(pre_val.issues)}"
                TaskStateManager.save_task(task)
                return task

            # 6.2 报告合成
            task.status = TaskStatus.SYNTHESIZING
            TaskStateManager.save_task(task)
            report = self._step_synthesize(task)

            # 6.3 合成后门禁质检 (绝无静默替换后门)
            post_val = PipelineValidator.validate_after_synthesis(
                report=report,
                valid_evidence_items=task.evidence_items
            )
            if not post_val.is_valid:
                logger.warning(f"合成后门禁首次核验未通过: {post_val.issues}，依据实施方案第148条尝试 1 次带反馈重新合成...")
                # 允许最多 1 次带具体审查错误信息的重新合成
                report = self._step_synthesize(task, error_feedback=post_val.issues)
                post_val_retry = PipelineValidator.validate_after_synthesis(
                    report=report,
                    valid_evidence_items=task.evidence_items
                )
                if not post_val_retry.is_valid:
                    logger.error(f"合成后门禁复检仍未通过: {post_val_retry.issues}")
                    task.status = TaskStatus.FAILED
                    task.error_message = f"合成后门禁校验未通过: {'; '.join(post_val_retry.issues)}"
                    task.report = None
                    TaskStateManager.save_task(task)
                    return task

            task.report = report

            # 6.4 终态判定：检查是否有客观缺口
            has_objective_gap = (
                bool(report.uncertainties_summary)
                or any(e.polarity == Polarity.UNVERIFIABLE for e in task.evidence_items)
                or any(sh.verdict_status == VerdictStatus.UNVERIFIABLE for sh in task.sub_hypotheses)
            )
            if has_objective_gap:
                task.status = TaskStatus.PARTIALLY_COMPLETED
                logger.info(f"任务 [{task_id}] 存在客观数据缺口，标记为 PARTIALLY_COMPLETED")
            else:
                task.status = TaskStatus.COMPLETED
                logger.info(f"任务 [{task_id}] 全部门禁通过，标记为 COMPLETED")

            TaskStateManager.save_task(task)
            return task

        except Exception as e:
            logger.error(f"流水线执行异常 [{task_id}]: {e}\n{traceback.format_exc()}")
            task.status = TaskStatus.FAILED
            task.error_message = str(e)
            TaskStateManager.save_task(task)
            return task

    # ==========================================================================
    # 流水线各阶段实现
    # ==========================================================================

    def _step_decompose(self, task: ResearchTask) -> None:
        """Step 1: 拆解命题为 2~4 个子假设与结构化取数计划"""
        user_prompt = f"""标的: {task.ticker} {task.ticker_name}
澄清后命题: {task.clarified_hypothesis}
分析区间: {task.analysis_start_date} 至 {task.analysis_end_date}

请将该命题拆解为 2~4 个独立、可证伪的子假设，并给出数据需求与精准公告检索关键词（notice_search_keywords）。
【重要提示】：所有子假设陈述必须严格针对本次动态指定的完整分析区间（{task.analysis_start_date} 至 {task.analysis_end_date}）进行证伪设计，严禁退缩为其他历史单一年份！"""

        decomp_res: DecomposerOutput = self.llm.call_structured(
            system_prompt=DECOMPOSER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=DecomposerOutput
        )

        task.sub_hypotheses = [
            SubHypothesis(
                id=item.id,
                dimension=item.dimension,
                statement=item.statement,
                verdict_status=VerdictStatus.UNVERIFIABLE,
                confidence_level=ConfidenceLevel.LOW
            )
            for item in decomp_res.sub_hypotheses
        ]
        task.meta["decompose_plan"] = [item.model_dump() for item in decomp_res.sub_hypotheses]
        logger.info(f"Step 1 完成，拆解出 {len(task.sub_hypotheses)} 个子假设")

    def _step_retrieve(self, task: ResearchTask) -> None:
        """Step 2: 调用真实数据客户端动态拉取原始数据并进行三态时间打标"""
        raw_list: List[RawRefRecord] = []
        raw_counter = 1

        sy_int = int(task.analysis_start_date[:4])
        ey_int = int(task.analysis_end_date[:4])
        curr_year = max(datetime.now().year, ey_int)

        # 动态确定季报拉取条数：扶摇财报接口采用从最新已披露报告期倒序 limit 语义。
        # 为确保完整覆盖历史同比基期 (sy_int - 1) 至目标分析期末 (ey_int)，拉取条数需以当前实际披露年份回溯至基期年：
        stmt_limit = min(24, max(16, (curr_year - (sy_int - 1) + 1) * 4))

        # 2.1 调取扶摇财报（利润表、资产负债表、现金流量表）
        # A. 利润表
        try:
            incomes = self.fuyao.get_income_statements(task.ticker, period="quarterly", limit=stmt_limit)
            task.meta["raw_income_statements_count"] = len(incomes) if isinstance(incomes, list) else 0
            if isinstance(incomes, list):
                for inc in incomes:
                    if not isinstance(inc, dict):
                        continue
                    fy = inc.get("fiscal_year")
                    fp = inc.get("fiscal_period")
                    if fy and fp:
                        rep_date = f"{fy}-{fp}"
                    else:
                        rep_date = inc.get("report_date") or inc.get("end_date") or "未知报告期"

                    op_inc = inc.get("operating_income")
                    op_cost = inc.get("operating_costs")
                    op_prof = inc.get("operating_profit")
                    net_profit = inc.get("parent_holder_net_profit")
                    rd_exp = inc.get("research_and_development_expenses")

                    raw_text = (
                        f"报告期: {rep_date}，营业收入: {op_inc}元，"
                        f"营业成本: {op_cost}元，营业利润: {op_prof}元，"
                        f"归属于母公司所有者的净利润: {net_profit}元"
                        f"{f'，研发费用: {rd_exp}元' if rd_exp is not None else ''}。"
                    )
                    raw_list.append(RawRefRecord(
                        raw_ref_id=f"RAW-INC-{raw_counter:03d}",
                        source_type=SourceType.FINANCIAL_STATEMENTS,
                        title=f"{task.ticker_name} {rep_date} 利润表(累计)",
                        publish_date=str(rep_date),
                        raw_text=raw_text
                    ))
                    raw_counter += 1
                    if str(rep_date) not in task.retrieved_periods:
                        task.retrieved_periods.append(str(rep_date))
        except Exception as e:
            logger.warning(f"获取扶摇利润表异常: {e}")

        # B. 资产负债表
        try:
            balance_sheets = self.fuyao.get_balance_sheets(task.ticker, period="quarterly", limit=stmt_limit)
            if isinstance(balance_sheets, list):
                for bs in balance_sheets:
                    if not isinstance(bs, dict):
                        continue
                    fy = bs.get("fiscal_year")
                    fp = bs.get("fiscal_period")
                    rep_date = f"{fy}-{fp}" if fy and fp else (bs.get("report_date") or "未知报告期")
                    assets = bs.get("assets_total")
                    cur_assets = bs.get("total_current_assets")
                    cash = bs.get("cash")
                    ar = bs.get("accounts_receivable")
                    debt = bs.get("total_debt")

                    raw_text = (
                        f"报告期: {rep_date} 资产负债表时点余额：资产总计: {assets}元，"
                        f"流动资产合计: {cur_assets}元，货币资金: {cash}元，"
                        f"应收账款: {ar}元，负债合计: {debt}元。"
                    )
                    raw_list.append(RawRefRecord(
                        raw_ref_id=f"RAW-BAL-{raw_counter:03d}",
                        source_type=SourceType.FINANCIAL_STATEMENTS,
                        title=f"{task.ticker_name} {rep_date} 资产负债表",
                        publish_date=str(rep_date),
                        raw_text=raw_text
                    ))
                    raw_counter += 1
        except Exception as e:
            logger.warning(f"获取扶摇资产负债表异常: {e}")

        # C. 现金流量表
        try:
            cash_flows = self.fuyao.get_cash_flow_statements(task.ticker, period="quarterly", limit=stmt_limit)
            if isinstance(cash_flows, list):
                for cf in cash_flows:
                    if not isinstance(cf, dict):
                        continue
                    fy = cf.get("fiscal_year")
                    fp = cf.get("fiscal_period")
                    rep_date = f"{fy}-{fp}" if fy and fp else (cf.get("report_date") or "未知报告期")
                    act_cf = cf.get("act_cash_flow_net")

                    raw_text = (
                        f"报告期: {rep_date} 现金流量表累计：经营活动产生的现金流量净额: {act_cf}元。"
                    )
                    raw_list.append(RawRefRecord(
                        raw_ref_id=f"RAW-CF-{raw_counter:03d}",
                        source_type=SourceType.FINANCIAL_STATEMENTS,
                        title=f"{task.ticker_name} {rep_date} 现金流量表",
                        publish_date=str(rep_date),
                        raw_text=raw_text
                    ))
                    raw_counter += 1
        except Exception as e:
            logger.warning(f"获取扶摇现金流量表异常: {e}")

        # 真实回填目标分析期内最新报告期 (latest_financial_period)
        # 严格遵守 AGENTS.md 第 2.2 节：区分目标分析期内财务期与后续新报告观察期
        target_periods = [p for p in task.retrieved_periods if int(p[:4]) <= ey_int]
        if target_periods:
            task.latest_financial_period = sorted(target_periods)[-1]
        elif task.retrieved_periods:
            task.latest_financial_period = sorted(task.retrieved_periods)[-1]

        # 2.2 调取扶摇五维财务指标（依据分析期动态设定，如 2023-4, 2024-3）
        indicator_reports = [f"{y}-4" for y in range(sy_int, ey_int + 1)]
        # 添加最新前置季报
        latest_quarter_rep = f"{ey_int}-3"
        if latest_quarter_rep not in indicator_reports:
            indicator_reports.append(latest_quarter_rep)

        for rep in indicator_reports:
            try:
                ind_data = self.fuyao.get_financial_indicators(task.ticker, report=rep)
                if ind_data and "abilities" in ind_data:
                    lines = [f"{task.ticker_name} {rep} 财务五维指标能力:"]
                    for ab in ind_data.get("abilities", []):
                        for item in ab.get("indicators", []):
                            idx_id = item.get("index_id")
                            val = item.get("value")
                            if idx_id and val is not None:
                                meta = INDEX_ID_CN_MAP.get(idx_id, {"name": idx_id, "unit": ""})
                                lines.append(f"{meta['name']}: {val}{meta['unit']}")
                    raw_text = "；".join(lines)
                    raw_list.append(RawRefRecord(
                        raw_ref_id=f"RAW-IND-{raw_counter:03d}",
                        source_type=SourceType.FINANCIAL_STATEMENTS,
                        title=f"{task.ticker_name} {rep} 五维财务指标",
                        publish_date=rep,
                        raw_text=raw_text
                    ))
                    raw_counter += 1
            except Exception as e:
                logger.warning(f"获取扶摇指标异常 [{rep}]: {e}")

        # 2.3 调取估值快照
        try:
            val_snap = self.fuyao.get_valuation_snapshot(task.ticker)
            if val_snap and any(val_snap.get(k) is not None for k in ("pe_ttm", "pb_mrq", "ps_ttm")):
                raw_text = (
                    f"{task.ticker_name} 最新估值快照：市盈率PE(TTM): {val_snap.get('pe_ttm')}倍，"
                    f"市净率PB(MRQ): {val_snap.get('pb_mrq')}倍，"
                    f"市销率PS(TTM): {val_snap.get('ps_ttm')}倍，"
                    f"市现率PCF(TTM): {val_snap.get('pcf_ttm')}倍。"
                )
                raw_list.append(RawRefRecord(
                    raw_ref_id=f"RAW-VAL-{raw_counter:03d}",
                    source_type=SourceType.FINANCIAL_STATEMENTS,
                    title=f"{task.ticker_name} 最新估值快照",
                    publish_date=task.analysis_end_date,
                    raw_text=raw_text
                ))
                raw_counter += 1
        except Exception as e:
            logger.warning(f"获取估值快照异常: {e}")

        # 2.4 调取个股历史K线与行业基准指数K线
        stock_ret: Optional[float] = None
        try:
            stock_bars = self.fuyao.get_stock_historical_prices(
                task.ticker,
                task.analysis_start_date,
                task.analysis_end_date
            )
            if stock_bars:
                s_open = stock_bars[0]["open_price"]
                s_close = stock_bars[-1]["close_price"]
                stock_ret = round(((s_close - s_open) / s_open) * 100, 2)
                raw_text = (
                    f"标的个股 {task.ticker_name} ({task.ticker}) 前复权历史行情在 "
                    f"{task.analysis_start_date} 至 {task.analysis_end_date} 区间内，"
                    f"期初开盘价 {s_open:.2f}元，期末收盘价 {s_close:.2f}元，区间收益率为 {stock_ret}%。"
                )
                raw_list.append(RawRefRecord(
                    raw_ref_id=f"RAW-PRIC-{raw_counter:03d}",
                    source_type=SourceType.FINANCIAL_STATEMENTS,
                    title=f"{task.ticker_name} 前复权历史行情与收益率",
                    publish_date=task.analysis_end_date,
                    raw_text=raw_text
                ))
                raw_counter += 1
        except Exception as e:
            logger.warning(f"获取个股历史行情异常: {e}")

        try:
            industry = self.fuyao.get_stock_industry(task.ticker)
            if industry:
                ind_code = industry.get("thscode")
                ind_name = industry.get("name")
                task.meta["industry_benchmark"] = industry
                k_bars = self.fuyao.get_index_historical_prices(
                    ind_code,
                    task.analysis_start_date,
                    task.analysis_end_date
                )
                if k_bars:
                    open_p = k_bars[0]["open_price"]
                    close_p = k_bars[-1]["close_price"]
                    ind_ret = round(((close_p - open_p) / open_p) * 100, 2)
                    raw_text = (
                        f"同花顺行业指数 {ind_name} ({ind_code}) 在 {task.analysis_start_date} 至 {task.analysis_end_date} 区间内，"
                        f"期初开盘点位 {open_p}，期末收盘点位 {close_p}，区间收益率为 {ind_ret}%。"
                    )
                    raw_list.append(RawRefRecord(
                        raw_ref_id=f"RAW-INDX-{raw_counter:03d}",
                        source_type=SourceType.FINANCIAL_STATEMENTS,
                        title=f"同花顺行业基准行情 ({ind_name})",
                        publish_date=task.analysis_end_date,
                        raw_text=raw_text
                    ))
                    raw_counter += 1
        except Exception as e:
            logger.warning(f"反查行业基准指数异常: {e}")

        # 2.5 调取 iFinD 法定公告 (从 Step 1 拆解计划动态收集关键词，严禁硬编码特定股票词汇)
        keywords_pool: List[str] = []
        for p in task.meta.get("decompose_plan", []):
            for kw in p.get("notice_search_keywords", []):
                if kw and kw not in keywords_pool:
                    keywords_pool.append(kw)
        
        if not keywords_pool:
            # 标的中立的通用财报/业务检索关键词
            keywords_pool = [
                "年度报告 主营业务 营业收入 产品结构",
                "财务报告 净利润 毛利率 研发投入"
            ]

        notice_queries = [
            f"{task.ticker_name} {kw}" if task.ticker_name not in kw else kw
            for kw in keywords_pool[:3]
        ]

        notice_start = f"{sy_int}-01-01"
        notice_end = min(datetime.now().strftime("%Y-%m-%d"), f"{ey_int + 1}-05-31")

        seen_notice_keys = set()
        for q in notice_queries:
            try:
                notices = self.ifind.search_notice(q, time_start=notice_start, time_end=notice_end, size=2)
                for noti in notices:
                    title = noti.get("title", "")
                    content = noti.get("content", "")
                    date_str = noti.get("date", "")
                    key = (title, date_str)
                    if content and key not in seen_notice_keys:
                        seen_notice_keys.add(key)
                        raw_list.append(RawRefRecord(
                            raw_ref_id=f"RAW-NOTI-{raw_counter:03d}",
                            source_type=SourceType.STATUTORY_NOTICE,
                            title=title,
                            publish_date=date_str,
                            raw_text=content
                        ))
                        raw_counter += 1
            except Exception as e:
                logger.warning(f"iFinD search_notice 异常 [{q}]: {e}")

        # 2.6 调取 iFinD 财务补充表格 (get_stock_financials)
        try:
            fin_supp = self.ifind.get_stock_financials(
                f"{task.ticker_name} {sy_int}年 {ey_int}年 扣除非经常性损益 净利润 存货"
            )
            if fin_supp.get("answer"):
                raw_list.append(RawRefRecord(
                    raw_ref_id=f"RAW-SUPP-{raw_counter:03d}",
                    source_type=SourceType.IFIND_TABLE_SYNTHESIS,
                    title=f"{task.ticker_name} iFinD 财务补充指标表格",
                    publish_date=task.analysis_end_date,
                    raw_text=fin_supp.get("answer")
                ))
                raw_counter += 1
        except Exception as e:
            logger.warning(f"iFinD get_stock_financials 异常: {e}")

        task.raw_records.extend(raw_list)
        logger.info(f"Step 2 完成，成功拉取并落盘 {len(raw_list)} 条真实原始材料记录")

    def _step_extract_and_compute(self, task: ResearchTask, is_replenish: bool = False) -> None:
        """Step 3: 双轨事实提取（结构化代码直读 + 非结构化防伪抽取）与确定性 Python 计算"""
        # ----------------------------------------------------------------------
        # 轨道 A: 结构化财务与指标材料 Python 代码直接读取（严禁交由 LLM 抄写）
        # ----------------------------------------------------------------------
        target_raws = task.raw_records if not is_replenish else [
            r for r in task.raw_records if r.raw_ref_id.startswith("RAW-REP-")
        ]

        fact_counter = len(task.extracted_facts) + 1
        existing_signatures = {(f.metric_name, f.period) for f in task.extracted_facts}

        for r in target_raws:
            if r.source_type == SourceType.FINANCIAL_STATEMENTS:
                text = r.raw_text
                # 1. 利润表直接抽取
                if "利润表" in r.title:
                    m_p = re.search(r"报告期:\s*([0-9]{4}-[A-Za-z0-9]+)", text)
                    period_str = m_p.group(1) if m_p else r.publish_date

                    patterns = [
                        ("营业收入", r"营业收入:\s*([-+]?\d+(?:\.\d+)?)元"),
                        ("营业成本", r"营业成本:\s*([-+]?\d+(?:\.\d+)?)元"),
                        ("营业利润", r"营业利润:\s*([-+]?\d+(?:\.\d+)?)元"),
                        ("归属于母公司所有者的净利润", r"归属于母公司所有者的净利润:\s*([-+]?\d+(?:\.\d+)?)元"),
                        ("研发费用", r"研发费用:\s*([-+]?\d+(?:\.\d+)?)元"),
                    ]
                    for name, pat in patterns:
                        m = re.search(pat, text)
                        if m and (name, period_str) not in existing_signatures:
                            raw_yuan = float(m.group(1))
                            val_yi, unit = UnitNormalizer.normalize_value_and_unit(raw_yuan, default_unit="元")
                            # exact_quote 取自原文完整句子
                            quote_match = re.search(rf"[^，。；]*{name}:[^，。；]*", text)
                            exact_quote = quote_match.group(0).strip() if quote_match else m.group(0)

                            task.extracted_facts.append(ExtractedFact(
                                fact_id=f"FACT-STRUC-{fact_counter:03d}",
                                raw_ref_id=r.raw_ref_id,
                                metric_name=name,
                                numeric_value=round(val_yi, 4),
                                unit=unit,
                                period=period_str,
                                exact_quote=exact_quote,
                                verified_in_raw=True
                            ))
                            fact_counter += 1
                            existing_signatures.add((name, period_str))

                # 2. 资产负债表与现金流量表直接抽取
                elif "资产负债表" in r.title:
                    m_p = re.search(r"报告期:\s*([0-9]{4}-[A-Za-z0-9]+)", text)
                    period_str = m_p.group(1) if m_p else r.publish_date
                    bs_patterns = [
                        ("资产总计", r"资产总计:\s*([-+]?\d+(?:\.\d+)?)元"),
                        ("应收账款", r"应收账款:\s*([-+]?\d+(?:\.\d+)?)元"),
                        ("货币资金", r"货币资金:\s*([-+]?\d+(?:\.\d+)?)元"),
                        ("负债合计", r"负债合计:\s*([-+]?\d+(?:\.\d+)?)元"),
                    ]
                    for name, pat in bs_patterns:
                        m = re.search(pat, text)
                        if m and (name, period_str) not in existing_signatures:
                            raw_yuan = float(m.group(1))
                            val_yi, unit = UnitNormalizer.normalize_value_and_unit(raw_yuan, default_unit="元")
                            quote_match = re.search(rf"[^，。；]*{name}:[^，。；]*", text)
                            exact_quote = quote_match.group(0).strip() if quote_match else m.group(0)

                            task.extracted_facts.append(ExtractedFact(
                                fact_id=f"FACT-STRUC-{fact_counter:03d}",
                                raw_ref_id=r.raw_ref_id,
                                metric_name=name,
                                numeric_value=round(val_yi, 4),
                                unit=unit,
                                period=period_str,
                                exact_quote=exact_quote,
                                verified_in_raw=True
                            ))
                            fact_counter += 1
                            existing_signatures.add((name, period_str))

                elif "现金流量表" in r.title:
                    m_p = re.search(r"报告期:\s*([0-9]{4}-[A-Za-z0-9]+)", text)
                    period_str = m_p.group(1) if m_p else r.publish_date
                    m = re.search(r"经营活动产生的现金流量净额:\s*([-+]?\d+(?:\.\d+)?)元", text)
                    if m and ("经营活动现金流净额", period_str) not in existing_signatures:
                        raw_yuan = float(m.group(1))
                        val_yi, unit = UnitNormalizer.normalize_value_and_unit(raw_yuan, default_unit="元")
                        quote_match = re.search(r"[^，。；]*经营活动产生的现金流量净额:[^，。；]*", text)
                        exact_quote = quote_match.group(0).strip() if quote_match else m.group(0)

                        task.extracted_facts.append(ExtractedFact(
                            fact_id=f"FACT-STRUC-{fact_counter:03d}",
                            raw_ref_id=r.raw_ref_id,
                            metric_name="经营活动现金流净额",
                            numeric_value=round(val_yi, 4),
                            unit=unit,
                            period=period_str,
                            exact_quote=exact_quote,
                            verified_in_raw=True
                        ))
                        fact_counter += 1
                        existing_signatures.add(("经营活动现金流净额", period_str))

                # 3. 财务指标直接抽取
                elif "财务指标" in r.title or "五维财务指标" in r.title:
                    ind_items = [
                        ("销售毛利率", r"销售毛利率:\s*([-+]?\d+(?:\.\d+)?)%"),
                        ("销售净利率", r"销售净利率:\s*([-+]?\d+(?:\.\d+)?)%"),
                        ("加权平均净资产收益率", r"加权平均净资产收益率:\s*([-+]?\d+(?:\.\d+)?)%"),
                        ("净利润现金含量", r"净利润现金含量:\s*([-+]?\d+(?:\.\d+)?)%"),
                        ("存货周转率", r"存货周转率:\s*([-+]?\d+(?:\.\d+)?)次"),
                        ("应收账款周转率", r"应收账款周转率:\s*([-+]?\d+(?:\.\d+)?)次"),
                    ]
                    for name, pat in ind_items:
                        m = re.search(pat, text)
                        if m and (name, r.publish_date) not in existing_signatures:
                            num = float(m.group(1))
                            unit = "%" if "%" in pat else "次"
                            quote_match = re.search(rf"[^，。；]*{name}:[^，。；]*", text)
                            exact_quote = quote_match.group(0).strip() if quote_match else m.group(0)

                            task.extracted_facts.append(ExtractedFact(
                                fact_id=f"FACT-STRUC-{fact_counter:03d}",
                                raw_ref_id=r.raw_ref_id,
                                metric_name=name,
                                numeric_value=num,
                                unit=unit,
                                period=r.publish_date,
                                exact_quote=exact_quote,
                                verified_in_raw=True
                            ))
                            fact_counter += 1
                            existing_signatures.add((name, r.publish_date))

                # 4. 行情收益率直接抽取
                elif "行情" in r.title or "收益率" in r.title:
                    m = re.search(r"区间收益率为\s*([-+]?\d+(?:\.\d+)?)%", text)
                    if m:
                        m_name = "个股区间收益率" if "个股" in r.title else "行业指数区间收益率"
                        if (m_name, r.publish_date) not in existing_signatures:
                            val = float(m.group(1))
                            quote_match = re.search(r"[^，。；]*区间收益率为\s*[-+]?\d+(?:\.\d+)?%[^，。；]*", text)
                            exact_quote = quote_match.group(0).strip() if quote_match else m.group(0)
                            task.extracted_facts.append(ExtractedFact(
                                fact_id=f"FACT-STRUC-{fact_counter:03d}",
                                raw_ref_id=r.raw_ref_id,
                                metric_name=m_name,
                                numeric_value=val,
                                unit="%",
                                period=f"{task.analysis_start_date[:4]}-{task.analysis_end_date[:4]}",
                                exact_quote=exact_quote,
                                verified_in_raw=True
                            ))
                            fact_counter += 1
                            existing_signatures.add((m_name, r.publish_date))

            elif r.source_type == SourceType.IFIND_TABLE_SYNTHESIS:
                # iFinD Markdown 表格直接逐行抽取为结构化事实
                rows = UnitNormalizer.parse_markdown_tables(r.raw_text)
                for row in rows:
                    date_cell = row.get("报告期") or row.get("截止日期") or row.get("时间")
                    date_str = date_cell.get("raw") if date_cell else ""
                    for col_name, cell_data in row.items():
                        if any(kw in col_name for kw in ("扣非", "非经常性损益", "存货")):
                            metric_name = "扣除非经常性损益净利润" if "扣非" in col_name or "非经常性损益" in col_name else "存货账面价值"
                            val = cell_data.get("value")
                            unit = cell_data.get("unit")
                            raw_c = cell_data.get("raw")
                            if val is not None and (metric_name, date_str) not in existing_signatures:
                                raw_line = row.get("_raw_line") or f"{raw_c}"
                                task.extracted_facts.append(ExtractedFact(
                                    fact_id=f"FACT-STRUC-{fact_counter:03d}",
                                    raw_ref_id=r.raw_ref_id,
                                    metric_name=metric_name,
                                    numeric_value=val,
                                    unit=unit,
                                    period=date_str,
                                    exact_quote=raw_line,
                                    verified_in_raw=True
                                ))
                                fact_counter += 1
                                existing_signatures.add((metric_name, date_str))

        # ----------------------------------------------------------------------
        # 轨道 B: 非结构化公告/新闻全量抽取 + 代码级四要素防伪核验
        # ----------------------------------------------------------------------
        textual_raws = [
            r for r in target_raws
            if r.source_type in (SourceType.STATUTORY_NOTICE, SourceType.NEWS_SUMMARY)
        ]

        if textual_raws:
            # 杜绝盲目截断，全部送入抽取池；按每批 2 条精炼送抽，控制单次提示词在 1500 字以内，杜绝 LLM 接口超时
            batch_size = 2
            for i in range(0, len(textual_raws), batch_size):
                batch = textual_raws[i:i + batch_size]
                raws_text_payload = "\n\n".join([
                    f"【材料 ID: {r.raw_ref_id} | 标题: {r.title} | 发布日期: {r.publish_date}】\n{r.raw_text[:700]}"
                    for r in batch
                ])

                user_prompt = f"""标的: {task.ticker_name}
请从以下给定的材料中，精选提取 2~4 项与公司主营业务、产品结构、产销量、出货量、提价、研发投入等最密切相关的核心原子事实（请控制在 4 项以内，输出完整 JSON 对象）。
【核心规则】：
1. 每个事实的 exact_quote 必须是一字不差从对应材料原文中摘录的原句，严禁修改任何标点符号、数字和小数点！
2. numeric_value 若非空，必须与 exact_quote 中提到的数字和单位保持严格一致！

材料内容：
{raws_text_payload}
"""
                try:
                    extract_res: FactExtractorOutput = self.llm.call_structured(
                        system_prompt=FACT_EXTRACTOR_SYSTEM_PROMPT,
                        user_prompt=user_prompt,
                        response_model=FactExtractorOutput,
                        max_tokens=3000
                    )

                    # 代码级四要素原文防伪核验
                    candidate_facts = [
                        ExtractedFact(
                            fact_id=f"FACT-TEXT-{fact_counter + idx:03d}",
                            raw_ref_id=it.raw_ref_id,
                            metric_name=it.metric_name,
                            numeric_value=it.numeric_value,
                            unit=it.unit,
                            period=it.period,
                            exact_quote=it.exact_quote,
                            verified_in_raw=False
                        )
                        for idx, it in enumerate(extract_res.extracted_facts)
                    ]

                    verified_facts, issues = PipelineValidator.validate_facts_against_raw(
                        raw_records=task.raw_records,
                        facts=candidate_facts
                    )
                    logger.info(f"批次公告防伪核验完成: 候选 {len(candidate_facts)} 项，通过 {len(verified_facts)} 项")
                    if issues:
                        logger.warning(f"防伪核验拦截无效事实: {issues[:2]}")

                    for vf in verified_facts:
                        task.extracted_facts.append(vf)
                        fact_counter += 1

                except Exception as e:
                    logger.error(f"批次文本事实抽取异常: {e}")

        # ----------------------------------------------------------------------
        # 3.2 确定性 Python 计算 (严格同比差 1 年 + 单季流量拆解 + 超额收益率)
        # ----------------------------------------------------------------------
        comp_list: List[ComputedMetric] = []
        comp_id_seq = len(task.computed_metrics) + 1
        existing_comp_names = {c.name for c in task.computed_metrics}

        # 1. 核心指标严格同比计算 (营业收入、归属于母公司所有者的净利润、经营现金流)
        metrics_for_yoy = ["营业收入", "归属于母公司所有者的净利润", "经营活动现金流净额"]
        for m_name in metrics_for_yoy:
            facts = [f for f in task.extracted_facts if f.metric_name == m_name and f.numeric_value is not None]
            for f1 in facts:
                for f2 in facts:
                    if f1.fact_id != f2.fact_id:
                        try:
                            c_year, c_suf = DeterministicCalculator.parse_period(f1.period)
                            b_year, b_suf = DeterministicCalculator.parse_period(f2.period)
                            # 严格聚焦目标分析期间：后续超出分析终止年的观察期单列，不混入主归因计算池
                            if c_year > ey_int:
                                continue
                            if c_suf == b_suf and c_year - b_year == 1:
                                c_name = f"{f1.period} {m_name}严格同比"
                                if c_name not in existing_comp_names:
                                    comp = DeterministicCalculator.calculate_yoy(
                                        curr_val=f1.numeric_value,
                                        curr_period=f1.period,
                                        base_val=f2.numeric_value,
                                        base_period=f2.period,
                                        metric_name=m_name,
                                        computation_id=f"COMP-{comp_id_seq:03d}",
                                        input_fact_ids=[f1.fact_id, f2.fact_id]
                                    )
                                    comp_list.append(comp)
                                    comp_id_seq += 1
                                    existing_comp_names.add(c_name)
                                    break
                        except Exception:
                            pass

        # 2. 个股相对行业超额收益率计算
        stock_facts = [f for f in task.extracted_facts if f.metric_name == "个股区间收益率"]
        indx_facts = [f for f in task.extracted_facts if f.metric_name == "行业指数区间收益率"]
        if stock_facts and indx_facts:
            s_f = stock_facts[0]
            i_f = indx_facts[0]
            c_name = f"{task.ticker_name}相对行业超额收益率"
            if c_name not in existing_comp_names:
                excess_val = round(s_f.numeric_value - i_f.numeric_value, 2)
                comp_list.append(ComputedMetric(
                    computation_id=f"COMP-{comp_id_seq:03d}",
                    name=c_name,
                    value=excess_val,
                    unit="%",
                    formula=f"{task.ticker_name}区间收益率 ({s_f.numeric_value}%) - 行业基准区间收益率 ({i_f.numeric_value}%)",
                    input_fact_ids=[s_f.fact_id, i_f.fact_id],
                    scope=f"区间超额收益率（{s_f.period}，注明复权口径，不将股价直接等同于经营归因）",
                    trace=f"{s_f.numeric_value} - {i_f.numeric_value} = {excess_val}"
                ))
                comp_id_seq += 1
                existing_comp_names.add(c_name)

        # 3. 研发费用率或费用占营收比计算
        rd_facts = [f for f in task.extracted_facts if f.metric_name == "研发费用" and f.numeric_value is not None]
        inc_facts = [f for f in task.extracted_facts if f.metric_name == "营业收入" and f.numeric_value is not None]
        if rd_facts and inc_facts:
            for rf in rd_facts:
                matching_inc = next((inf for inf in inc_facts if inf.period == rf.period), None)
                if matching_inc:
                    c_name = f"{rf.period} 研发费用率"
                    if c_name not in existing_comp_names:
                        try:
                            comp_ratio = DeterministicCalculator.calculate_ratio(
                                numerator_val=rf.numeric_value,
                                denominator_val=matching_inc.numeric_value,
                                metric_name=c_name,
                                computation_id=f"COMP-{comp_id_seq:03d}",
                                input_fact_ids=[rf.fact_id, matching_inc.fact_id]
                            )
                            comp_list.append(comp_ratio)
                            comp_id_seq += 1
                            existing_comp_names.add(c_name)
                        except Exception as e:
                            logger.warning(f"研发费用率计算失败: {e}")

        task.computed_metrics.extend(comp_list)
        logger.info(
            f"Step 3 完成，当前累计核验事实 {len(task.extracted_facts)} 项，确定性计算 {len(task.computed_metrics)} 项"
        )

    def _step_check_sufficiency(self, task: ResearchTask) -> Tuple[bool, List[Any]]:
        """Step 4: 覆盖度检查与定向补检决策"""
        facts_summary = [
            f"[{f.fact_id}] {f.period} {f.metric_name}: {f.numeric_value} {f.unit} (引文: {f.exact_quote[:40]})"
            for f in task.extracted_facts
        ]
        comps_summary = [
            f"[{c.computation_id}] {c.name}: {c.value}{c.unit} (Trace: {c.trace})"
            for c in task.computed_metrics
        ]
        subs_summary = [
            f"[{s.id}] [{s.dimension}] {s.statement}"
            for s in task.sub_hypotheses
        ]

        user_prompt = f"""标的: {task.ticker_name} ({task.ticker})
子假设列表：
{chr(10).join(subs_summary)}

已核验事实清单：
{chr(10).join(facts_summary) if facts_summary else '暂无'}

已执行确定性计算清单：
{chr(10).join(comps_summary) if comps_summary else '暂无'}

请评估当前证据对上述子假设的验证充分性。若存在关键事实缺失且可通过进一步精确检索法定公告弥补，请给出补检计划（最多1轮）。
"""
        try:
            res: SufficiencyOutput = self.llm.call_structured(
                system_prompt=SUFFICIENCY_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_model=SufficiencyOutput
            )
            task.meta["sufficiency_gap_notes"] = res.gap_notes
            return res.needs_replenish, res.replenish_queries
        except Exception as e:
            logger.error(f"覆盖度审查异常: {e}")
            return False, []

    def _step_replenish(self, task: ResearchTask, queries: List[Any]) -> None:
        """Step 4 补检执行（定向顺延回溯检索，回溯12个月集中在编排层计算）"""
        logger.info(f"触发定向补检，补检查询数: {len(queries)}")
        raw_counter = len(task.raw_records) + 1
        sy = task.analysis_start_date[:4]
        ey = task.analysis_end_date[:4]

        # 补检回溯前溯 1 年，窗口终止至次年 5 月底
        time_start = f"{int(sy) - 1}-01-01"
        time_end = min(datetime.now().strftime("%Y-%m-%d"), f"{int(ey) + 1}-05-31")

        for q_item in queries[:2]:  # 最多执行 2 条精准补检
            q_text = getattr(q_item, "query", str(q_item))
            if task.ticker_name not in q_text:
                q_text = f"{task.ticker_name} {q_text}"
            try:
                notices = self.ifind.search_notice(
                    query=q_text,
                    time_start=time_start,
                    time_end=time_end,
                    size=2,
                    is_replenish=True
                )
                for noti in notices:
                    if noti.get("content"):
                        task.raw_records.append(RawRefRecord(
                            raw_ref_id=f"RAW-REP-{raw_counter:03d}",
                            source_type=SourceType.STATUTORY_NOTICE,
                            title=f"[补检] {noti.get('title', '')}",
                            publish_date=noti.get("date"),
                            raw_text=noti.get("content", "")
                        ))
                        raw_counter += 1
            except Exception as e:
                logger.warning(f"定向补检检索异常 [{q_text}]: {e}")

    def _step_classify(self, task: ResearchTask) -> None:
        """Step 5: 组织证据卡片，审慎归类并更新每个子假设的结论与置信度"""
        sy_int = int(task.analysis_start_date[:4])
        ey_int = int(task.analysis_end_date[:4])
        relevant_years = {str(y) for y in range(sy_int - 1, ey_int + 1)}

        # 优先选取分析期内与基期的事实，以及所有公告事实
        target_facts = [
            f for f in task.extracted_facts
            if f.fact_id.startswith("FACT-TEXT-")
            or any(yr in str(f.period) for yr in relevant_years)
        ][:60]

        facts_summary = [
            f"事实ID: {f.fact_id} | 来源材料ID: {f.raw_ref_id} | 报告期: {f.period} | 指标: {f.metric_name} | 值: {f.numeric_value} {f.unit} | 引文: {f.exact_quote}"
            for f in target_facts
        ]

        # 优先选取相关期间计算指标
        target_comps = [
            c for c in task.computed_metrics
            if any(yr in str(c.name) or yr in str(c.scope) for yr in relevant_years) or "超额" in c.name
        ][:35]

        comps_summary = [
            f"计算ID: {c.computation_id} | 指标: {c.name} | 结果: {c.value}{c.unit} | 公式: {c.formula} | 输入事实: {c.input_fact_ids}"
            for c in target_comps
        ]
        subs_summary = [
            f"子假设ID: {s.id} | 维度: {s.dimension} | 陈述: {s.statement}"
            for s in task.sub_hypotheses
        ]

        user_prompt = f"""标的: {task.ticker_name} ({task.ticker})
动态分析区间: {task.analysis_start_date} 至 {task.analysis_end_date}
子假设列表：
{chr(10).join(subs_summary)}

已核验事实列表：
{chr(10).join(facts_summary)}

确定性计算指标列表：
{chr(10).join(comps_summary)}

请根据审慎归因原则，将事实与计算结果归纳为证据卡片，归入对应的子假设，并严格赋予支持（SUPPORT）、反对（AGAINST）或无法验证（UNVERIFIABLE）极性。
【重要要求】：请全面考察并引用整个动态分析区间（{task.analysis_start_date} 至 {task.analysis_end_date}）内的有效事实与计算，不得仅局限于某一特定历史年份。
【铁律】：
1. inherited_source_ids 严格只能填入实际存在的 raw_ref_id！
2. fact_ids 严格只能填入实际存在的 fact_id！
"""
        try:
            res: ClassifierOutput = self.llm.call_structured(
                system_prompt=CLASSIFIER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_model=ClassifierOutput,
                max_tokens=3500
            )

            valid_raw_ids = {r.raw_ref_id for r in task.raw_records}
            valid_fact_ids = {f.fact_id for f in task.extracted_facts}
            valid_comp_ids = {c.computation_id for c in task.computed_metrics}

            evidence_items: List[EvidenceItem] = []
            for item in res.evidence_items:
                clean_raw_ids = [rid for rid in item.inherited_source_ids if rid in valid_raw_ids]
                clean_fact_ids = [fid for fid in item.fact_ids if fid in valid_fact_ids]
                clean_comp_id = item.computation_id if item.computation_id in valid_comp_ids else None

                # 若缺少来源但有事实，推导补齐 raw_ref_id
                if not clean_raw_ids and clean_fact_ids:
                    for cf in clean_fact_ids:
                        for ef in task.extracted_facts:
                            if ef.fact_id == cf and ef.raw_ref_id in valid_raw_ids:
                                clean_raw_ids.append(ef.raw_ref_id)
                    clean_raw_ids = list(dict.fromkeys(clean_raw_ids))

                if not clean_raw_ids:
                    continue

                evid = EvidenceItem(
                    evidence_id=item.evidence_id,
                    sub_id=item.sub_id,
                    title=item.title,
                    summary=item.summary,
                    polarity=item.polarity,
                    source_type=item.source_type,
                    inherited_source_ids=clean_raw_ids,
                    fact_ids=clean_fact_ids,
                    computation_id=clean_comp_id,
                    exact_quote=item.exact_quote,
                    weaked_sub_aspect=item.weaked_sub_aspect,
                    unverifiable_reason=item.unverifiable_reason
                )
                evidence_items.append(evid)

            task.evidence_items = evidence_items

            # ------------------------------------------------------------------
            # 真实回写更新每个 SubHypothesis 的判定状态、置信度与证据绑定
            # ------------------------------------------------------------------
            for sh in task.sub_hypotheses:
                sh_supports = [e.evidence_id for e in evidence_items if e.sub_id == sh.id and e.polarity == Polarity.SUPPORT]
                sh_againsts = [e.evidence_id for e in evidence_items if e.sub_id == sh.id and e.polarity == Polarity.AGAINST]
                sh_unverifiables = [e.evidence_id for e in evidence_items if e.sub_id == sh.id and e.polarity == Polarity.UNVERIFIABLE]

                sh.supported_evidence_ids = list(dict.fromkeys(sh_supports))
                sh.against_evidence_ids = list(dict.fromkeys(sh_againsts))
                sh.unverifiable_evidence_ids = list(dict.fromkeys(sh_unverifiables))

                sup_c = len(sh.supported_evidence_ids)
                ag_c = len(sh.against_evidence_ids)
                unv_c = len(sh.unverifiable_evidence_ids)

                if ag_c > 0 and ag_c >= sup_c:
                    sh.verdict_status = VerdictStatus.REFUTED
                    sh.confidence_level = ConfidenceLevel.HIGH if ag_c >= 2 else ConfidenceLevel.MEDIUM
                elif sup_c > 0:
                    # 审慎归因：若取得事实支撑但缺乏经审计分部利润直接拆解，审慎维持部分支持
                    sh.verdict_status = VerdictStatus.PARTIALLY_SUPPORTED
                    sh.confidence_level = ConfidenceLevel.HIGH if sup_c >= 3 else ConfidenceLevel.MEDIUM
                else:
                    sh.verdict_status = VerdictStatus.UNVERIFIABLE
                    sh.confidence_level = ConfidenceLevel.LOW

                # 回写证据缺口说明
                if unv_c > 0 or sh.verdict_status == VerdictStatus.PARTIALLY_SUPPORTED:
                    sh.evidence_gap_note = "公开披露具备整体经营与驱动解释一致性证据，但缺乏经审计的细分单品/业务线利润增量直接拆分账目。"
                elif sh.verdict_status == VerdictStatus.UNVERIFIABLE:
                    sh.evidence_gap_note = "当前公开财报与公告中暂未检索到支撑该子假设的直接定量证据。"

            logger.info(f"Step 5 完成，成功归纳生成 {len(evidence_items)} 张规范证据卡片并回写各子假设状态")
        except Exception as e:
            logger.error(f"证据卡片归类异常: {e}")

    def _step_synthesize(self, task: ResearchTask, error_feedback: Optional[List[str]] = None) -> FinalReport:
        """Step 6: 合成结构化研究报告（严禁后门篡改与洗白修补）"""
        evid_summary = [
            f"[{e.evidence_id}] [{e.sub_id}] 极性: {e.polarity.value} | 标题: {e.title} | 摘要: {e.summary} | 引文: {e.exact_quote or ''}"
            for e in task.evidence_items
        ]
        subs_summary = [
            f"[{s.id}] [{s.dimension}] {s.statement} (结论: {s.verdict_status.value}, 置信度: {s.confidence_level.value}, 支持证据: {s.supported_evidence_ids}, 反对: {s.against_evidence_ids}, 缺口: {s.evidence_gap_note or '无'})"
            for s in task.sub_hypotheses
        ]

        feedback_block = ""
        if error_feedback:
            feedback_block = f"\n\n【注意：上一轮质检未通过，请严格纠正以下问题】：\n" + "\n".join(f"- {issue}" for issue in error_feedback)

        user_prompt = f"""标的: {task.ticker_name} ({task.ticker})
命题: {task.clarified_hypothesis}
分析区间: {task.analysis_start_date} 至 {task.analysis_end_date}

子假设验证概况：
{chr(10).join(subs_summary)}

全部有效证据卡片：
{chr(10).join(evid_summary)}

请合成最终的研究证伪报告。
【铁律】：
1. 报告的客观事实摘要（facts_summary）、审慎推论（inferences_summary）必须严格聚焦并反映本次动态设定的完整分析区间（{task.analysis_start_date} 至 {task.analysis_end_date}），严禁擅自退回或局限在某一特定历史单一年份；
2. 区分客观事实、审慎推论与客观缺口；
3. 反转触发条件必须同时包含 HISTORICAL_ATTRIBUTION 与 FUTURE_SUSTAINABILITY 两类，并附带实质 analytical_basis；
4. cited_evidence_ids 必须且只能包含本次提供的有效证据 ID！严禁编造任何未提供的证据 ID！{feedback_block}
"""
        res: SynthesizerOutput = self.llm.call_structured(
            system_prompt=SYNTHESIZER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=SynthesizerOutput,
            max_tokens=3500
        )

        # 严禁用 valid_eids[:5] 静默修补或过滤幻觉 ID，直接原汁原味接受门禁质检
        report = FinalReport(
            verdict_status=res.verdict_status,
            confidence_level=res.confidence_level,
            verdict_headline=res.verdict_headline,
            facts_summary=res.facts_summary,
            inferences_summary=res.inferences_summary,
            uncertainties_summary=res.uncertainties_summary,
            conflict_resolution=res.conflict_resolution,
            falsification_triggers=res.falsification_triggers,
            cited_evidence_ids=res.cited_evidence_ids,
            compliance_disclaimer=res.compliance_disclaimer
        )
        return report
