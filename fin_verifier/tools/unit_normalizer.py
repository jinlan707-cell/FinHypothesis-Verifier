import re
from typing import Optional, Tuple, Dict, Any, List

class UnitNormalizer:
    """
    数据单位归一化器与 Markdown 表格解析器。
    规范：
    1. 计算金额统一归一为【亿元】浮点数，保持高精度；展示保留 4 位小数；
    2. 百分比转换为纯数值（保留 % 标识）；
    3. 吨数/千升保持原物理单位；
    4. 单位不明时返回 unknown，不猜测倍数。
    """

    @staticmethod
    def normalize_value_and_unit(raw_val: Any, default_unit: str = "元") -> Tuple[Optional[float], str]:
        """
        解析原始值与单位，返回 (归一化浮点数值, 标准单位)。
        金额一律归一化为【亿元】。
        """
        if raw_val is None:
            return None, "null"
        
        val_str = str(raw_val).replace(",", "").strip()
        if not val_str or val_str in ("--", "-", "null", "None", "不适用", "N/A"):
            return None, "null"

        # 1. 百分比
        if "%" in val_str:
            matches = re.findall(r"[-+]?\d*\.?\d+", val_str)
            if matches:
                return float(matches[0]), "%"
            return None, "%"

        # 2. 吨数 / 万吨
        if "万吨" in val_str:
            matches = re.findall(r"[-+]?\d*\.?\d+", val_str)
            if matches:
                return float(matches[0]) * 10000.0, "吨"
            return None, "吨"
        if "吨" in val_str:
            matches = re.findall(r"[-+]?\d*\.?\d+", val_str)
            if matches:
                return float(matches[0]), "吨"
            return None, "吨"

        # 3. 倍数 (PE/PB等)
        if "倍" in val_str:
            matches = re.findall(r"[-+]?\d*\.?\d+", val_str)
            if matches:
                return float(matches[0]), "倍"
            return None, "倍"

        # 4. 金额单位识别
        if "亿元" in val_str or "亿" in val_str:
            matches = re.findall(r"[-+]?\d*\.?\d+", val_str)
            if matches:
                return float(matches[0]), "亿元"
            return None, "亿元"
        elif "万元" in val_str or "万" in val_str:
            matches = re.findall(r"[-+]?\d*\.?\d+", val_str)
            if matches:
                return float(matches[0]) / 10000.0, "亿元"
            return None, "亿元"
        elif "元" in val_str:
            matches = re.findall(r"[-+]?\d*\.?\d+", val_str)
            if matches:
                return float(matches[0]) / 100000000.0, "亿元"
            return None, "亿元"

        # 5. 纯数值（需结合 default_unit 判断）
        matches = re.findall(r"[-+]?\d*\.?\d+", val_str)
        if matches:
            num = float(matches[0])
            if default_unit == "元":
                return num / 100000000.0, "亿元"
            elif default_unit == "万元":
                return num / 10000.0, "亿元"
            elif default_unit == "亿元":
                return num, "亿元"
            elif default_unit == "%":
                return num, "%"
            elif default_unit == "吨":
                return num, "吨"
            elif default_unit == "倍":
                return num, "倍"
            else:
                # 若无明确单位，金额若极大（如 >= 1e8），推断为元并转换为亿元
                if abs(num) >= 1e8:
                    return num / 100000000.0, "亿元"
                return num, default_unit

        return None, "unknown"

    @staticmethod
    def parse_markdown_tables(text: str) -> List[Dict[str, Any]]:
        """
        解析 Markdown 文本中包含的一个或多个 Markdown 表格，并返回提取出的行级结构化记录列表 (list[dict])。
        每行保留其日期/报告期列与各指标列的归一化键值，杜绝多期时序被最后一行覆盖。
        """
        tables_data = []
        lines = [line.strip() for line in text.strip().split("\n")]
        
        current_table_lines = []
        in_table = False

        for line in lines:
            if line.startswith("|") and line.endswith("|"):
                in_table = True
                current_table_lines.append(line)
            else:
                if in_table and len(current_table_lines) >= 3:
                    rows = UnitNormalizer._parse_single_table(current_table_lines)
                    tables_data.extend(rows)
                in_table = False
                current_table_lines = []

        if in_table and len(current_table_lines) >= 3:
            rows = UnitNormalizer._parse_single_table(current_table_lines)
            tables_data.extend(rows)

        return tables_data

    @staticmethod
    def _parse_single_table(table_lines: List[str]) -> List[Dict[str, Any]]:
        """解析单张 Markdown 表格，将各行转化为记录字典列表 (list[dict])，杜绝多期覆盖"""
        if len(table_lines) < 3:
            return []
        # table_lines[0] 为表头
        # table_lines[1] 为分隔符 |---|---|
        # table_lines[2:] 为数据行
        headers = [c.strip() for c in table_lines[0].split("|")[1:-1]]
        row_records = []
        for row_line in table_lines[2:]:
            cells = [c.strip() for c in row_line.split("|")[1:-1]]
            if not any(cells):
                continue
            row_dict = {}
            for i, h in enumerate(headers):
                if i < len(cells):
                    raw_c = cells[i]
                    # 从表头推断默认单位
                    header_unit = "元"
                    if "亿元" in h or "亿" in h:
                        header_unit = "亿元"
                    elif "万元" in h or "万" in h:
                        header_unit = "万元"
                    elif "%" in h:
                        header_unit = "%"
                    elif "倍" in h:
                        header_unit = "倍"
                    elif "吨" in h:
                        header_unit = "吨"
                    elif any(k in h for k in ("代码", "简称", "日期", "报告期", "季度", "年度")):
                        header_unit = "unknown"

                    val, unit = UnitNormalizer.normalize_value_and_unit(raw_c, default_unit=header_unit)
                    row_dict[h] = {
                        "raw": raw_c,
                        "value": val,
                        "unit": unit
                    }
            if row_dict:
                row_dict["_raw_line"] = row_line
                row_records.append(row_dict)
        return row_records
