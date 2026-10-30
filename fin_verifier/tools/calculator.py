import ast
import re
import math
from typing import Dict, Any, Tuple, Optional
from fin_verifier.core.models import ComputedMetric

class CalculationError(Exception):
    """确定性计算异常"""
    pass

class SafeASTEvaluator:
    """
    白名单 AST 安全求值器。
    严禁裸 eval，严禁函数调用、属性访问与任意外部导入。
    仅允许算术四则运算与预定义变量求值。
    """
    ALLOWED_NODE_TYPES = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Constant,
        ast.Name,
        ast.Load,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Pow,
        ast.USub,
        ast.UAdd,
    )

    @classmethod
    def evaluate(cls, expression: str, variables: Dict[str, float]) -> float:
        if not expression or len(expression) > 200:
            raise CalculationError(f"表达式为空或长度超过限制 (<=200): '{expression}'")

        try:
            tree = ast.parse(expression.strip(), mode="eval")
        except Exception as e:
            raise CalculationError(f"无法解析表达式语法: '{expression}', 错误: {e}")

        for node in ast.walk(tree):
            if not isinstance(node, cls.ALLOWED_NODE_TYPES):
                raise CalculationError(f"表达式包含非法操作或节点类型: {type(node).__name__}")

        def _eval_node(node: ast.AST) -> float:
            if isinstance(node, ast.Expression):
                return _eval_node(node.body)
            elif isinstance(node, ast.Constant):
                if isinstance(node.value, (int, float)):
                    return float(node.value)
                raise CalculationError(f"不支持的常量类型: {type(node.value)}")
            elif isinstance(node, ast.Name):
                var_name = node.id
                if var_name not in variables:
                    raise CalculationError(f"未提供变量值: '{var_name}'")
                val = variables[var_name]
                if val is None or not math.isfinite(val):
                    raise CalculationError(f"变量 '{var_name}' 值为非有限数: {val}")
                return float(val)
            elif isinstance(node, ast.UnaryOp):
                operand_val = _eval_node(node.operand)
                if isinstance(node.op, ast.USub):
                    return -operand_val
                elif isinstance(node.op, ast.UAdd):
                    return operand_val
                raise CalculationError(f"不支持的一元运算符: {type(node.op)}")
            elif isinstance(node, ast.BinOp):
                left_val = _eval_node(node.left)
                right_val = _eval_node(node.right)
                if isinstance(node.op, ast.Add):
                    return left_val + right_val
                elif isinstance(node.op, ast.Sub):
                    return left_val - right_val
                elif isinstance(node.op, ast.Mult):
                    return left_val * right_val
                elif isinstance(node.op, ast.Div):
                    if abs(right_val) < 1e-12:
                        raise CalculationError("除数为零或极其接近零")
                    return left_val / right_val
                elif isinstance(node.op, ast.Pow):
                    if right_val > 5:
                        raise CalculationError("乘方幂次过高 (>5)")
                    return left_val ** right_val
                raise CalculationError(f"不支持的二元运算符: {type(node.op)}")
            raise CalculationError(f"未知节点: {type(node)}")

        res = _eval_node(tree.body)
        if not math.isfinite(res):
            raise CalculationError(f"计算结果非有限数值: {res}")
        return res


class DeterministicCalculator:
    """
    双模确定性计算器。
    严格满足：
    1. 同比校验：报告期后缀必须完全一致且年份严格相差 1 年；
    2. 结果精度与单位保留；
    3. 生成可追溯 trace 与 computation_id 闭环。
    """

    @staticmethod
    def parse_period(period_str: str) -> Tuple[int, str]:
        """
        解析报告期，如 '2023-FY' -> (2023, 'FY')，'2024-Q3' -> (2024, 'Q3')，'2023' -> (2023, 'FY')
        """
        period_clean = period_str.strip().upper()
        match = re.search(r"(\d{4})[-年/]?([A-Z0-9]*)", period_clean)
        if not match:
            raise CalculationError(f"无法识别报告期格式: '{period_str}'")
        year = int(match.group(1))
        suffix = match.group(2).replace("季度", "Q").replace("年", "FY")
        if not suffix:
            suffix = "FY"
        return year, suffix

    @staticmethod
    def calculate_yoy(
        curr_val: float,
        curr_period: str,
        base_val: float,
        base_period: str,
        metric_name: str,
        computation_id: str,
        input_fact_ids: list[str]
    ) -> ComputedMetric:
        """
        严格同比差 1 年计算器。
        """
        c_year, c_suffix = DeterministicCalculator.parse_period(curr_period)
        b_year, b_suffix = DeterministicCalculator.parse_period(base_period)

        if c_suffix != b_suffix:
            raise CalculationError(
                f"同比报告期后缀不一致: 当期 '{curr_period}' ({c_suffix}) vs 基期 '{base_period}' ({b_suffix})"
            )
        if c_year - b_year != 1:
            raise CalculationError(
                f"同比年份未严格相差 1 年: 当期年份 {c_year} vs 基期年份 {b_year} (差值 {c_year - b_year})"
            )
        if abs(base_val) < 1e-12:
            raise CalculationError(f"基期数值为 0，无法计算同比增速: 基期={base_val}")

        # 计算同比变动百分比
        growth_rate = ((curr_val - base_val) / abs(base_val)) * 100.0
        rounded_rate = round(growth_rate, 4)

        base_note = ""
        if base_val < 0:
            base_note = " [注: 基期为负值，表示亏损/负向收窄或转正]"

        formula = "((本期值 - 基期值) / abs(基期值)) * 100"
        trace = f"(({curr_val} - {base_val}) / abs({base_val})) * 100 = {rounded_rate}%{base_note}"

        return ComputedMetric(
            computation_id=computation_id,
            name=f"{metric_name}同比增速",
            value=rounded_rate,
            unit="%",
            scope=f"{c_suffix}严格同比(差1年)",
            formula=formula,
            trace=trace,
            input_fact_ids=input_fact_ids
        )

    @staticmethod
    def calculate_ratio(
        numerator_val: float,
        denominator_val: float,
        metric_name: str,
        computation_id: str,
        input_fact_ids: list[str],
        as_percentage: bool = True
    ) -> ComputedMetric:
        """
        结构占比或比率计算器（如直销收入占比 = 直销收入 / 营业收入 * 100）
        """
        if abs(denominator_val) < 1e-12:
            raise CalculationError("分母为零，无法计算比率")

        raw_ratio = numerator_val / denominator_val
        if as_percentage:
            val = round(raw_ratio * 100.0, 4)
            unit = "%"
            formula = "(分子 / 分母) * 100"
            trace = f"({numerator_val} / {denominator_val}) * 100 = {val}%"
        else:
            val = round(raw_ratio, 4)
            unit = "倍"
            formula = "分子 / 分母"
            trace = f"{numerator_val} / {denominator_val} = {val}"

        return ComputedMetric(
            computation_id=computation_id,
            name=metric_name,
            value=val,
            unit=unit,
            scope="比率分析",
            formula=formula,
            trace=trace,
            input_fact_ids=input_fact_ids
        )

    @staticmethod
    def calculate_custom_ast(
        expression: str,
        variables: Dict[str, float],
        metric_name: str,
        computation_id: str,
        input_fact_ids: list[str],
        unit: str = "%",
        scope: str = "自定义推导"
    ) -> ComputedMetric:
        """
        受 AST 白名单约束的安全自定义计算
        """
        val = SafeASTEvaluator.evaluate(expression, variables)
        rounded_val = round(val, 4)
        
        # 组装代入过程 trace
        trace_expr = expression
        for k, v in variables.items():
            trace_expr = re.sub(rf"\b{k}\b", str(v), trace_expr)
        trace = f"{trace_expr} = {rounded_val} {unit}"

        return ComputedMetric(
            computation_id=computation_id,
            name=metric_name,
            value=rounded_val,
            unit=unit,
            scope=scope,
            formula=expression,
            trace=trace,
            input_fact_ids=input_fact_ids
        )
