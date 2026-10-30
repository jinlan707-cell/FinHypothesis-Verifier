# FinHypothesis-Verifier AI 工具使用与验证记录 (AI_USAGE_RECORD.md)

> **项目名称**：FinHypothesis-Verifier（投资命题与多证据验证系统）  
> **基准标的**：【300308.SZ 中际旭创】（AI 算力光模块龙头）  
> **完成日期**：2026-10-30  
> **记录规范**：依据《AGENTS.md》第五章“AI 使用、验收与交付流程”要求如实编写。

---

## 一、 系统架构与 AI 角色定位

本项目定位为**单命题、单公司深度验证工作台**，扮演一名**中立、严苛、只认真实审计账目与法定披露的金融事实查验员（Fact-Checker）**。系统遵循“因果解构 + 多源对账 + 审慎证据链 + 波普尔证伪主义”范式，将职责清晰界定在两个平面：

| 参与实体 | 承担职责与边界 | 防范的核心风险 |
| :--- | :--- | :--- |
| **SiliconFlow DeepSeek-V4.1-Flash** | 命题合规澄清（Step 0）、2~4 个独立子假设拆解（Step 1）、非结构化公告原子事实抽取（Step 3 轨道 B）、证据组织与审慎归类（Step 5）、研报合成与反转触发条件归纳（Step 6）、已存事实追问解答（Step 7） | 严禁利用通用记忆凭空编造财务数字；严禁进行未经审计支持的越级因果推断 |
| **确定性规则层 (Python AST + 双阶段门禁)** | 财报三表结构化数据直读与单位归一（轨道 A）、严格同比差 1 年核验、AST 白名单安全四则运算、单季流量拆解、保留标点原文逐字与数值防伪核验、违规投资敏感词过滤 | 防范 LLM 算术计算幻觉、除以零、提示词注入与越界代码执行 |
| **执行开发 Agent (AI Coding Agent)** | P0/P1/P2 漏洞全面修复、去硬编码中立化重构、真实物理接口协议对齐、状态机闭环与单任务原子落盘、自动化测试套件编写与交付验证 | 严格遵守工程红线，保持代码与实施方案 v6.1 完全一致，排除预设伪数据与门禁洗白后门 |

---

## 二、 关键开发环节与人机修正记录

在系统实施过程中，AI 开发 Agent 与真实环境、真实上游接口进行了严格的实测对账，以下记录核心实测发现与人工/架构修正过程：

### 1. 全面去硬编码与反洗白门禁重构（P0 级阻断修复）
- **现象记录**：审查发现旧实现中存在多处硬编码：公告检索写死了白酒关键词，研报合成模型的校验器中存在自动注入茅台直销渠道反转条件与固定免责声明的后门，合成前存在用截断切片强制修补合法 ID 冒充门禁通过的偷步逻辑。
- **修正措施**：
  1. `fin_verifier/core/pipeline.py`：移除所有写死的专有词，公告检索关键词完全动态读取 Step 1 拆解计划生成的 `task.meta["decompose_plan"]["notice_search_keywords"]`；
  2. `fin_verifier/prompts/synthesizer_prompt.py`：移除 `SynthesizerOutput.normalize_fields` 中的自动洗白与后门注入逻辑，反转条件必须完全由模型依据真实有效证据生成；
  3. `fin_verifier/core/pipeline.py`：移除用 `valid_eids[:5]` 静默修补 `cited_evidence_ids` 的后门，门禁未通过时依据方案第 148 条仅允许 1 次带具体错误信息的重新合成，仍未通过直接标记 `FAILED`。
- **验证级别**：`verified`（通过 `test_synthesizer_output_no_auto_injection_backdoor` 与真实全链路质检门禁验证）。

### 2. 真实物理接口修复与单季拆解对齐（P0 级阻断修复）
- **现象记录**：扶摇开放平台实际接口返回结构与预设方案存在多处微观差异：
  - 扶摇资产负债表与现金流量表字段为整型 `fiscal_year` 与字符串 `fiscal_period`（'Q1'~'Q4'），并无 `report_date` 字符串；
  - 扶摇指标接口返回 `abilities[*].indicators[*].{index_id, value}`，无 `name` 字段，读取 `item.get('name')` 会产生空指标；
  - iFinD 返回多行 Markdown 时序表时，单 dict 赋值会导致历史数据被最后一行完全覆盖。
- **修正措施**：
  1. `fuyao_client.py`：单季流量拆解重构为优先按 `fiscal_year` 与 `fiscal_period` 匹配同年相邻期相减；
  2. `fuyao_client.py`：增加完整的指标 ID 中文映射字典 `INDEX_ID_CN_MAP`，准确解析五维财务指标；修复估值快照接口对 `data.item` 字典的解包；
  3. `unit_normalizer.py`：重构 Markdown 表格解析器，将表格解析为 `list[dict]` 逐行时序记录列表，保留时序多期。
- **验证级别**：`verified`（通过 `test_parse_markdown_tables_multi_row_no_overwrite` 与 `test_fuyao_disaggregate_quarterly_flow_fiscal_structure` 验证）。

### 3. 双轨事实抽取与直接计算（P0 级阻断修复）
- **现象记录**：旧实现对检索到的材料设置了 `if len(...) >= 4: break` 的盲目截断，并将结构化财务数据交由 LLM 抄写。
- **修正措施**：
  1. 删除盲目截断，全部真实材料送入抽取池；
  2. 实施“双轨事实提取”：轨道 A 由 Python 代码直接读取扶摇三表财报字段，统一归一为【亿元】浮点数；轨道 B 将公告与新闻按精炼批次送入 LLM 提取原子事实，并接受代码级原文与数值防伪核验。
- **验证级别**：`verified`（在中际旭创真实全链路中直接提取并核验 156 项事实与 60 项计算指标）。

### 4. 原文防伪与子假设状态闭环（P1 级严重修复）
- **现象记录**：旧核验仅核对引文是否存在，未核对抽取数值是否与引文数值一致；Step 5 证据归类后未回写更新子假设，导致所有子假设终身滞留为初始未验证状态。
- **修正措施**：
  1. `fin_verifier/core/validator.py`：强化 `validate_facts_against_raw`，在保留标点子串核验基础上，从引文中抽取数值候选池进行严格比对（支持千分位与单位换算容差），并核验报告期上下文；
  2. `fin_verifier/core/pipeline.py`：在 Step 5 依据归属证据状态真实回写更新每个 `SubHypothesis` 的 `verdict_status`、`confidence_level` 和 `evidence_gap_note`；
  3. 定向补检获取新材料后，状态显式切回 `EXTRACTING_AND_COMPUTING` 重新流转抽取与计算，严禁跳步。
- **验证级别**：`verified`（通过 `test_validator_numeric_tampering_with_real_quote` 与全链路任务 JSON 验证）。

### 5. 接口契约合规与重试防放大（P1 级严重修复）
- **现象记录**：初始化 OpenAI 客户端时未配置 `max_retries=0`，导致 SDK 内部隐式重试与外层退避装饰器重叠放大超时；部分接口存在茅台 Mock 兜底。
- **修正措施**：
  1. 初始化 `OpenAI(...)` 显式设置 `max_retries=0`，重试由退避装饰器集中控制；
  2. `/api/demos` 切换为中际旭创基准；`/api/tickers/search` 与 `/api/clarify` 移除 Mock 兜底，真实透传异常；
  3. 服务启动时增加 `cleanup_orphan_tasks()`，将异常中断的非终态僵尸任务统一收敛至 `FAILED`。
- **验证级别**：`verified`（通过 `test_cleanup_orphan_tasks` 与 `test_api_verify_validation_rules` 验证）。

### 6. 前端渲染安全与防 XSS（P2 级优化修复）
- **现象记录**：前端直接拼接 `innerHTML` 插入来自上游或 LLM 的不可信文本，存在 XSS 注入风险；步进器缺少 `REPLENISHING` 状态展示；缺少子假设独立卡片。
- **修正措施**：
  1. `app.js`：增加 `escapeHtml` 机制，全面转义动态 DOM 文本；
  2. 步进器补齐 `REPLENISHING`、`PARTIALLY_COMPLETED` 与 `FAILED` 全量状态机展示；
  3. 增加子假设状态独立卡片网格，展示每个子假设的研究维度、判定、置信度与证据缺口；
  4. 移除绕过 Step 0 直启全量验证的偷步旁路按钮。
- **验证级别**：`implemented`（前端代码与样式全部重构就绪）。

---

## 三、 完成证据分级汇总

根据 `AGENTS.md` 完成证据分级准则（`reported` / `implemented` / `verified`）：

| 模块 / 功能项 | 交付内容 | 证据级别 | 验证方式与复现路径 |
| :--- | :--- | :---: | :--- |
| **基础配置与领域模型** | `fin_verifier/core/models.py`<br>`fin_verifier/core/config.py` | `verified` | `pytest tests/test_models_and_config.py` 通过；强 ID 溯源闭环落盘验证通过 |
| **工具层与计算器** | `resilience.py`, `calculator.py`<br>`unit_normalizer.py`, `fuyao_client.py`<br>`ifind_client.py`, `llm_client.py` | `verified` | `pytest tests/test_tools_unit.py` 全部通过；实测扶摇行情反查与 iFinD MCP 公告检索通过 |
| **两阶段质检门禁** | `fin_verifier/core/validator.py` | `verified` | `pytest tests/test_pipeline_and_gates.py` 全部通过；防伪篡改拦截与合规敏感词拦截通过 |
| **全量 9 个 REST API** | `fin_verifier/main.py` | `verified` | `pytest tests/test_api_integration.py` 离线与在线测试全部通过 |
| **Web 前端现代化工作台** | `fin_verifier/web/index.html`<br>`fin_verifier/web/style.css`<br>`fin_verifier/web/app.js` | `implemented` | 静态单页应用、8 节点步进器、子假设卡片网格、证据穿透抽屉、追问控制台全部就位 |
| **中际旭创全链路端到端** | `script/run_real_pipeline.py`<br>`fin_verifier/data/tasks/` | `verified` | 真实调取 300308.SZ 财报与法定公告，产出 `task-real-20261030135349.json`，两阶段质检门禁均已通过 |

---

## 四、 结论与合规声明

整个系统在实现过程中严格贯彻：
1. **真实数据驱动**：生产运行不伪造上游返回，不使用 Mock 数据兜底冒充真实金融事实；
2. **审慎因果归因**：在未取得细分产品直接毛利直接审计账目前，结论审慎限定为反证（REFUTED）或部分支持（PARTIALLY_SUPPORTED），保留客观数据缺口；
3. **合规免责保护**：系统严格过滤“建议买入”、“目标价”等荐股诱导词汇，明确声明本产品仅供投研事实对账使用，不构成任何投资建议。
