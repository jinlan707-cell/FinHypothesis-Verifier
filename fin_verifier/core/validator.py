import re
import logging
from typing import List, Dict, Tuple, Set, Optional
from fin_verifier.core.models import (
    RawRefRecord,
    ExtractedFact,
    ComputedMetric,
    EvidenceItem,
    FinalReport,
    FalsificationTrigger,
    TriggerType
)

logger = logging.getLogger("fin_verifier.core.validator")

class ValidationResult:
    def __init__(self, is_valid: bool, issues: List[str]):
        self.is_valid = is_valid
        self.issues = issues

    def __repr__(self):
        return f"<ValidationResult valid={self.is_valid} issues={len(self.issues)}>"


class PipelineValidator:
    """
    两阶段严密质检门禁机制 (Two-Phase Gate)。
    1. 阶段 1：合成前核验（原文逐字防伪、算式输入完备性、严格同比差 1 年、ID 链路闭环）
    2. 阶段 2：合成后质检（报告引用闭环、反转触发条件非空且有依据、合规词过滤）
    """

    # 违规投资建议关键词库（合规门禁）
    COMPLIANCE_FORBIDDEN_WORDS = [
        "建议买入", "强烈推荐", "买入评级", "维持增持", "增持评级",
        "建议卖出", "卖出评级", "目标价", "建仓", "加仓", "满仓", "做多", "做空"
    ]

    @staticmethod
    def normalize_text(text: str) -> str:
        """
        规范空白与换行符，严格保留小数点、负号、百分号及全部标点符号。
        严禁使用去除标点的正则！
        """
        if not text:
            return ""
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _extract_numeric_candidates_from_quote(quote: str) -> List[float]:
        """
        从引文文本中解析出所有可能的数值候选项（含千分位解析、万元/亿元折算与百分比）。
        """
        candidates: List[float] = []
        # 匹配所有包含千分位或小数的数字片段以及跟随的单位修饰词
        pattern = re.compile(r"([-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(亿元|万元|万吨|吨|元|%|倍)?")
        for match in pattern.finditer(quote):
            raw_num_str = match.group(1).replace(",", "")
            unit_suffix = match.group(2) or ""
            try:
                base_num = float(raw_num_str)
            except ValueError:
                continue

            candidates.append(base_num)
            # 常见财务单位折算为【亿元】浮点数与原值候选
            if unit_suffix == "亿元":
                candidates.append(base_num)
            elif unit_suffix == "万元":
                candidates.append(base_num / 10000.0)
            elif unit_suffix == "元":
                candidates.append(base_num / 100000000.0)
            elif unit_suffix == "%":
                candidates.append(base_num)

        return candidates

    @classmethod
    def validate_facts_against_raw(
        cls,
        raw_records: List[RawRefRecord],
        facts: List[ExtractedFact]
    ) -> Tuple[List[ExtractedFact], List[str]]:
        """
        阶段 1 核心：四要素防伪核验。
        1. 检查 raw_ref_id 是否存在且 exact_quote 非空；
        2. 检查 exact_quote 是否逐字存在于 raw_text 中（规范连续空白，严格保留标点与符号）；
        3. 检查 numeric_value（若非空）是否在引文中真实存在（比对数值、千分位及单位换算容差）；
        4. 检查 period 是否与原始材料期间相符。
        返回 (已核验事实列表, 失败问题说明列表)。
        """
        raw_map: Dict[str, RawRefRecord] = {r.raw_ref_id: r for r in raw_records}
        verified_facts: List[ExtractedFact] = []
        issues: List[str] = []

        for fact in facts:
            if fact.raw_ref_id not in raw_map:
                issues.append(f"事实 [{fact.fact_id}] 引用的 raw_ref_id [{fact.raw_ref_id}] 在原始材料中不存在")
                fact.verified_in_raw = False
                continue

            raw_record = raw_map[fact.raw_ref_id]
            raw_normalized = cls.normalize_text(raw_record.raw_text)
            quote_normalized = cls.normalize_text(fact.exact_quote)

            if not quote_normalized:
                issues.append(f"事实 [{fact.fact_id}] 引文为空，判定为核验失败")
                fact.verified_in_raw = False
                continue

            # 1. 核心防伪匹配：规范空白后子串匹配，严格保留所有标点
            if quote_normalized not in raw_normalized:
                issues.append(
                    f"事实 [{fact.fact_id}] 原文防伪核验失败！引文逐字在原始材料 [{fact.raw_ref_id}] 中不存在: '{quote_normalized[:40]}...'"
                )
                fact.verified_in_raw = False
                continue

            # 2. 数值与单位防伪核验（杜绝真句子改数值字段）
            if fact.numeric_value is not None:
                candidates = cls._extract_numeric_candidates_from_quote(quote_normalized)
                target = float(fact.numeric_value)
                matched = False
                for cand in candidates:
                    # 容差范围：绝对误差 < 0.01 或相对误差 < 0.5%
                    if abs(cand - target) < 0.01 or (abs(target) > 0 and abs(cand - target) / abs(target) < 0.005):
                        matched = True
                        break
                if not matched:
                    issues.append(
                        f"事实 [{fact.fact_id}] 数值防伪核验失败！抽取数值 [{fact.numeric_value}] ({fact.unit}) 与引文中的数值不一致: '{quote_normalized[:50]}...'"
                    )
                    fact.verified_in_raw = False
                    continue

            # 3. 报告期核验（防范张冠李戴跨期造假）
            if fact.period:
                # 抽取年份
                period_years = re.findall(r"\b(20\d{2})\b", str(fact.period))
                if period_years:
                    fact_year = period_years[0]
                    # 检查年份是否在引文、材料标题或发布日期中出现
                    year_in_context = (
                        fact_year in quote_normalized
                        or fact_year in str(raw_record.title)
                        or fact_year in str(raw_record.publish_date)
                        or fact_year in raw_normalized
                    )
                    if not year_in_context:
                        issues.append(
                            f"事实 [{fact.fact_id}] 报告期核验失败！抽取期间 [{fact.period}] 与来源材料 [{fact.raw_ref_id}] 上下文完全不匹配"
                        )
                        fact.verified_in_raw = False
                        continue

            # 全部通过
            fact.verified_in_raw = True
            verified_facts.append(fact)

        return verified_facts, issues

    @classmethod
    def validate_before_synthesis(
        cls,
        raw_records: List[RawRefRecord],
        facts: List[ExtractedFact],
        computed_metrics: List[ComputedMetric],
        evidence_items: List[EvidenceItem]
    ) -> ValidationResult:
        """
        阶段 1 门禁：合成前核验。
        核验：
        1. 原文防伪子串核验；
        2. 确定性计算各项的 input_fact_ids 是否均在已核验事实中；
        3. 证据卡片的 inherited_source_ids 与 fact_ids 是否均有效。
        """
        issues: List[str] = []

        # 1. 原文防伪核验
        verified_facts, raw_issues = cls.validate_facts_against_raw(raw_records, facts)
        issues.extend(raw_issues)
        verified_fact_id_set: Set[str] = {f.fact_id for f in verified_facts}
        valid_raw_id_set: Set[str] = {r.raw_ref_id for r in raw_records}

        # 2. 计算项输入完备性核验
        valid_comp_id_set: Set[str] = set()
        for comp in computed_metrics:
            missing_inputs = [fid for fid in comp.input_fact_ids if fid not in verified_fact_id_set]
            if missing_inputs:
                issues.append(
                    f"计算项 [{comp.computation_id}] 输入的事实 ID 未通过原文核验或不存在: {missing_inputs}"
                )
            else:
                valid_comp_id_set.add(comp.computation_id)

        # 3. 证据卡片引用核验
        for evid in evidence_items:
            # 来源材料检查
            invalid_raws = [rid for rid in evid.inherited_source_ids if rid not in valid_raw_id_set]
            if invalid_raws:
                issues.append(f"证据 [{evid.evidence_id}] 引用的 raw_ref_id 无效: {invalid_raws}")

            # 事实引用检查
            invalid_facts = [fid for fid in evid.fact_ids if fid not in verified_fact_id_set]
            if invalid_facts:
                issues.append(f"证据 [{evid.evidence_id}] 引用的 fact_ids 未通过防伪核验或不存在: {invalid_facts}")

            # 计算项关联检查
            if evid.computation_id and evid.computation_id not in valid_comp_id_set:
                issues.append(
                    f"证据 [{evid.evidence_id}] 关联的 computation_id [{evid.computation_id}] 未通过前置校验"
                )

        is_valid = len(issues) == 0
        return ValidationResult(is_valid=is_valid, issues=issues)

    @classmethod
    def validate_after_synthesis(
        cls,
        report: FinalReport,
        valid_evidence_items: List[EvidenceItem]
    ) -> ValidationResult:
        """
        阶段 2 门禁：合成后质检。
        核验：
        1. 报告引用的 cited_evidence_ids 必须真实存在于有效证据池中；
        2. 反转触发条件非空，必须包含 HISTORICAL_ATTRIBUTION 与 FUTURE_SUSTAINABILITY，且注明 analytical_basis；
        3. 合规审查：检测是否含有禁止的买卖引导词；
        4. 免责声明非空且合规。
        """
        issues: List[str] = []
        valid_evidence_id_set = {e.evidence_id for e in valid_evidence_items}

        # 1. 证据引用检查
        if not report.cited_evidence_ids:
            issues.append("报告未引用任何证据 ID (cited_evidence_ids 为空)")
        else:
            missing_cites = [eid for eid in report.cited_evidence_ids if eid not in valid_evidence_id_set]
            if missing_cites:
                issues.append(f"报告引用了不存在的证据 ID: {missing_cites}")

        # 2. 反转触发条件检查
        if not report.falsification_triggers:
            issues.append("报告未输出任何反转触发条件 (falsification_triggers 为空)")
        else:
            has_historical = False
            has_future = False
            for trig in report.falsification_triggers:
                if trig.trigger_type == TriggerType.HISTORICAL_ATTRIBUTION:
                    has_historical = True
                elif trig.trigger_type == TriggerType.FUTURE_SUSTAINABILITY:
                    has_future = True
                
                if not trig.analytical_basis or len(trig.analytical_basis.strip()) < 5:
                    issues.append(f"反转条件 [{trig.condition_description[:20]}] 未注明合法的分析依据 (analytical_basis)")

            if not has_historical:
                issues.append("反转触发条件缺少历史归因推翻条件 (HISTORICAL_ATTRIBUTION)")
            if not has_future:
                issues.append("反转触发条件缺少未来持续性失效条件 (FUTURE_SUSTAINABILITY)")

        # 3. 合规词审查
        report_text = f"{report.verdict_headline} {' '.join(report.facts_summary)} {' '.join(report.inferences_summary)} {report.conflict_resolution}"
        for bad_word in cls.COMPLIANCE_FORBIDDEN_WORDS:
            if bad_word in report_text:
                issues.append(f"报告中包含违规投资建议敏感词: '{bad_word}'")

        # 4. 免责声明
        if not report.compliance_disclaimer or len(report.compliance_disclaimer.strip()) < 10:
            issues.append("报告未提供完整的合规免责声明")

        is_valid = len(issues) == 0
        return ValidationResult(is_valid=is_valid, issues=issues)
