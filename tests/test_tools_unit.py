import pytest
from fin_verifier.tools.resilience import retry_with_backoff, TransientAPIError, NonRetryableAPIError
from fin_verifier.tools.unit_normalizer import UnitNormalizer
from fin_verifier.tools.calculator import (
    SafeASTEvaluator,
    DeterministicCalculator,
    CalculationError
)
from fin_verifier.tools.fuyao_client import FuyaoClient

# ==============================================================================
# 1. 重试与弹性机制测试
# ==============================================================================

def test_retry_success_after_failure():
    calls = 0

    @retry_with_backoff(max_attempts=3, initial_delay=0.01, backoff_factor=1.0)
    def flaky_func():
        nonlocal calls
        calls += 1
        if calls < 2:
            raise TransientAPIError("暂时性网络超时")
        return "success"

    res = flaky_func()
    assert res == "success"
    assert calls == 2

def test_retry_non_retryable_immediate_raise():
    calls = 0

    @retry_with_backoff(max_attempts=3, initial_delay=0.01)
    def auth_failed():
        nonlocal calls
        calls += 1
        raise NonRetryableAPIError("401 Unauthorized")

    with pytest.raises(NonRetryableAPIError):
        auth_failed()
    assert calls == 1

def test_retry_exhaust_max_attempts():
    calls = 0

    @retry_with_backoff(max_attempts=3, initial_delay=0.01, backoff_factor=1.0)
    def always_fail():
        nonlocal calls
        calls += 1
        raise TransientAPIError("429 Too Many Requests")

    with pytest.raises(TransientAPIError):
        always_fail()
    assert calls == 3


# ==============================================================================
# 2. 单位归一化与 Markdown 表格解析测试
# ==============================================================================

def test_unit_normalizer_amounts():
    # 亿元
    val, u = UnitNormalizer.normalize_value_and_unit("1505.60亿元")
    assert val == 1505.6
    assert u == "亿元"

    # 万元 -> 转换为亿元
    val, u = UnitNormalizer.normalize_value_and_unit("50000.0万元")
    assert val == 5.0
    assert u == "亿元"

    # 元 -> 转换为亿元
    val, u = UnitNormalizer.normalize_value_and_unit("147693604994元")
    assert round(val, 2) == 1476.94
    assert u == "亿元"

    # 百分比
    val, u = UnitNormalizer.normalize_value_and_unit("18.04%")
    assert val == 18.04
    assert u == "%"

    # 吨数
    val, u = UnitNormalizer.normalize_value_and_unit("42795吨")
    assert val == 42795.0
    assert u == "吨"

    # 估值倍数
    val, u = UnitNormalizer.normalize_value_and_unit("25.3倍")
    assert val == 25.3
    assert u == "倍"

    # 空值与缺失
    val, u = UnitNormalizer.normalize_value_and_unit("--")
    assert val is None
    assert u == "null"

def test_parse_markdown_tables():
    md = """
| 证券简称 | 报告期 | 营业收入(元) | 销售毛利率(%) |
| :--- | :--- | :--- | :--- |
| 贵州茅台 | 2024-Q3 | 123123000000 | 91.53% |
"""
    tables = UnitNormalizer.parse_markdown_tables(md)
    assert len(tables) == 1
    t = tables[0]
    assert "营业收入(元)" in t
    assert t["营业收入(元)"]["unit"] == "亿元"
    assert round(t["营业收入(元)"]["value"], 2) == 1231.23
    assert t["销售毛利率(%)"]["value"] == 91.53
    assert t["销售毛利率(%)"]["unit"] == "%"

def test_parse_markdown_tables_multi_row_no_overwrite():
    # 验证 5 行时序表格不会被单 dict 循环赋值覆盖为仅剩最后一行
    md = """
| 证券代码 | 证券简称 | 报告期 | 扣非净利润(亿元) | 存货(亿元) |
| :--- | :--- | :--- | :--- | :--- |
| 300308.SZ | 中际旭创 | 20241231 | 50.68 | 70.51 |
| 300308.SZ | 中际旭创 | 20240930 | 37.18 | 71.53 |
| 300308.SZ | 中际旭创 | 20240630 | 23.33 | 61.69 |
| 300308.SZ | 中际旭创 | 20240331 | 9.90 | 52.11 |
| 300308.SZ | 中际旭创 | 20231231 | 21.24 | 42.95 |
"""
    rows = UnitNormalizer.parse_markdown_tables(md)
    assert len(rows) == 5
    # 验证各行报告期完全保留，前4行没有被最后一行 20231231 覆盖
    dates = [r["报告期"]["raw"] for r in rows]
    assert dates == ["20241231", "20240930", "20240630", "20240331", "20231231"]
    assert rows[0]["扣非净利润(亿元)"]["value"] == 50.68
    assert rows[4]["扣非净利润(亿元)"]["value"] == 21.24


# ==============================================================================
# 3. 严格同比差 1 年测试
# ==============================================================================

def test_deterministic_calculator_yoy_success():
    comp = DeterministicCalculator.calculate_yoy(
        curr_val=1505.60,
        curr_period="2023-FY",
        base_val=1275.54,
        base_period="2022-FY",
        metric_name="营业总收入",
        computation_id="COMP-YOY-01",
        input_fact_ids=["F-1", "F-2"]
    )
    assert comp.computation_id == "COMP-YOY-01"
    assert comp.value == 18.0363
    assert comp.unit == "%"
    assert "严格同比" in comp.scope
    assert comp.input_fact_ids == ["F-1", "F-2"]

def test_deterministic_calculator_yoy_period_mismatch():
    # 后缀不一致 (FY 对 Q3)
    with pytest.raises(CalculationError, match="同比报告期后缀不一致"):
        DeterministicCalculator.calculate_yoy(
            curr_val=1500.0,
            curr_period="2023-FY",
            base_val=1200.0,
            base_period="2022-Q3",
            metric_name="收入",
            computation_id="COMP-01",
            input_fact_ids=[]
        )

def test_deterministic_calculator_yoy_year_diff_not_one():
    # 年份未严格差 1 年 (2024 对 2022，差 2 年)
    with pytest.raises(CalculationError, match="同比年份未严格相差 1 年"):
        DeterministicCalculator.calculate_yoy(
            curr_val=1500.0,
            curr_period="2024-FY",
            base_val=1200.0,
            base_period="2022-FY",
            metric_name="收入",
            computation_id="COMP-01",
            input_fact_ids=[]
        )

def test_deterministic_calculator_ratio():
    comp = DeterministicCalculator.calculate_ratio(
        numerator_val=672.33,
        denominator_val=1476.94,
        metric_name="直销渠道收入占比",
        computation_id="COMP-RATIO-01",
        input_fact_ids=["F-DIRECT", "F-TOTAL"]
    )
    assert round(comp.value, 2) == 45.52
    assert comp.unit == "%"


# ==============================================================================
# 4. AST 白名单安全求值器测试
# ==============================================================================

def test_safe_ast_evaluator_valid():
    vars_map = {"a": 100.0, "b": 25.0}
    # 算术求值
    res = SafeASTEvaluator.evaluate("(a + b) / 2.0", vars_map)
    assert res == 62.5

    # 负数求值
    res_neg = SafeASTEvaluator.evaluate("-a + b", vars_map)
    assert res_neg == -75.0

def test_safe_ast_evaluator_security_blocks():
    # 拦截函数调用
    with pytest.raises(CalculationError, match="非法操作"):
        SafeASTEvaluator.evaluate("__import__('os').system('dir')", {})

    # 拦截属性访问
    with pytest.raises(CalculationError, match="非法操作"):
        SafeASTEvaluator.evaluate("a.__class__", {"a": 10})

    # 拦截除以零
    with pytest.raises(CalculationError, match="除数为零"):
        SafeASTEvaluator.evaluate("100 / 0", {})


# ==============================================================================
# 5. 累计流量拆解为单季度数值测试
# ==============================================================================

def test_fuyao_disaggregate_quarterly_flow():
    # 模拟 2023 年 4 个财报的营业收入 (年初至今累计值，原币元)
    statements = [
        {"report_date": "2023-03-31", "operating_income": 39379000000},  # Q1: 393.79亿
        {"report_date": "2023-06-30", "operating_income": 70987000000},  # Q2: 709.87亿 (单季=316.08)
        {"report_date": "2023-09-30", "operating_income": 105316000000}, # Q3: 1053.16亿 (单季=343.29)
        {"report_date": "2023-12-31", "operating_income": 150560000000}, # FY: 1505.60亿 (单季=452.44)
    ]
    res = FuyaoClient.disaggregate_quarterly_flow(statements, "operating_income")
    assert "2023-Q1" in res
    assert round(res["2023-Q1"], 2) == 393.79
    assert round(res["2023-Q2"], 2) == 316.08
    assert round(res["2023-Q3"], 2) == 343.29
    assert round(res["2023-Q4"], 2) == 452.44

def test_fuyao_disaggregate_quarterly_flow_fiscal_structure():
    # 验证真实扶摇接口整型 fiscal_year 与字符串 fiscal_period 结构拆解
    statements = [
        {"fiscal_year": 2024, "fiscal_period": "Q1", "operating_income": 4843000000},   # 48.43亿
        {"fiscal_year": 2024, "fiscal_period": "Q2", "operating_income": 10799000000},  # 107.99亿 (单季=59.56)
        {"fiscal_year": 2024, "fiscal_period": "Q3", "operating_income": 17313000000},  # 173.13亿 (单季=65.14)
        {"fiscal_year": 2024, "fiscal_period": "Q4", "operating_income": 23865000000},  # 238.65亿 (单季=65.52)
    ]
    res = FuyaoClient.disaggregate_quarterly_flow(statements, "operating_income")
    assert "2024-Q1" in res
    assert round(res["2024-Q1"], 2) == 48.43
    assert round(res["2024-Q2"], 2) == 59.56
    assert round(res["2024-Q3"], 2) == 65.14
    assert round(res["2024-Q4"], 2) == 65.52
