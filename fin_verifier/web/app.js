// ==============================================================================
// FinHypothesis-Verifier 投研证伪工作台前端交互核心 (HTML 安全转义与全状态闭环)
// ==============================================================================

let currentTaskId = null;
let currentTaskData = null;
let pollTimer = null;
let demosCache = [];

const TOOLTIP_TEXTS = {
  // 结论判定状态
  SUPPORTED: "【充分证实 / 支持】有直接、充分、无矛盾的法定披露或财务账目支持该假设，因果链条严密闭环。",
  PARTIALLY_SUPPORTED: "【部分支持 / 一致】具备宏观方向一致性证据（如营收大增），但缺乏细分产品利润直接审计账目，审慎防范越级归因。",
  REFUTED: "【被证伪 / 反驳】存在明确反向事实或关键财务指标相抵触（如综合毛利提升有限，研发费用压缩贡献更大）。",
  UNVERIFIABLE: "【客观无法验证 / 存疑】现有公开披露缺少必要数据，坚持材料不足不妄断，不调用模型外部记忆胡乱猜测。",
  
  // 置信度
  HIGH: "【高置信度】至少有 2~3 项经代码交叉校验的财务核心指标或法定公文直接印证。",
  MEDIUM: "【中置信度】依据合并报表整体推导、或有单项反向数据支撑，但缺少单品审计拆解明细。",
  LOW: "【低置信度】核心公开数据缺失，信息存在明显盲区，通常对应无法验证项。",
  
  // 反转条件
  HISTORICAL_ATTRIBUTION: "【历史归因推翻条件】若后续补充披露经审计的分部单品账目，证实某一因素确实贡献主导利润，则当前被反驳结论将被推翻并重新支持。",
  FUTURE_SUSTAINABILITY: "【未来持续性失效条件】即便当前盈利亮眼，未来在行业价格战或毛利率下滑、现金流背离时，该盈利驱动模式即告失效。"
};

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

document.addEventListener("DOMContentLoaded", () => {
  initDemos();
  setupTickerSearchAutocomplete();
});

// 1. 初始化预置典型命题模板
async function initDemos() {
  try {
    const res = await fetch("/api/demos");
    if (res.ok) {
      demosCache = await res.json();
    }
  } catch (e) {
    console.error("加载 Demo 列表失败:", e);
  }
}

function loadDemo(demoId) {
  const demo = demosCache.find(d => d.id === demoId);
  if (!demo) return;

  document.querySelectorAll(".btn-demo").forEach(b => b.classList.remove("active"));
  if (window.event && window.event.target) {
    window.event.target.classList.add("active");
  }

  document.getElementById("inputTicker").value = `${demo.ticker} ${demo.ticker_name}`;
  document.getElementById("inputStartDate").value = demo.analysis_start_date;
  document.getElementById("inputEndDate").value = demo.analysis_end_date;
  const periodDatesText = document.getElementById("clarifyPeriodDatesText");
  if (periodDatesText) {
    periodDatesText.innerText = `${demo.analysis_start_date} 至 ${demo.analysis_end_date}`;
  }
  document.getElementById("inputHypothesis").value = demo.hypothesis;
  document.getElementById("clarifyResultBox").classList.add("hidden");
  document.getElementById("pipelineErrorBanner").classList.add("hidden");
  document.getElementById("resultsContainer").classList.add("hidden");
}

// 2. 标的检索与消歧自动补全
function setupTickerSearchAutocomplete() {
  const input = document.getElementById("inputTicker");
  const dropdown = document.getElementById("tickerCandidatesDropdown");
  let debounceTimer = null;

  input.addEventListener("input", () => {
    clearTimeout(debounceTimer);
    const q = input.value.trim();
    if (!q || q.length < 1) {
      dropdown.classList.add("hidden");
      return;
    }

    debounceTimer = setTimeout(async () => {
      try {
        const res = await fetch(`/api/tickers/search?q=${encodeURIComponent(q)}`);
        if (!res.ok) {
          dropdown.classList.add("hidden");
          return;
        }
        const items = await res.json();
        if (items && items.length > 0) {
          dropdown.innerHTML = items.map(it => `
            <div class="dropdown-item" onclick="selectTicker('${escapeHtml(it.thscode)}', '${escapeHtml(it.name)}')">
              <span><strong>${escapeHtml(it.name)}</strong> (${escapeHtml(it.thscode)})</span>
              <span style="color:#64748b;font-size:11px;">${escapeHtml(it.market || '')}</span>
            </div>
          `).join("");
          dropdown.classList.remove("hidden");
        } else {
          dropdown.classList.add("hidden");
        }
      } catch (e) {
        console.error("标的消歧查询异常:", e);
      }
    }, 300);
  });

  document.addEventListener("click", (e) => {
    if (!input.contains(e.target) && !dropdown.contains(e.target)) {
      dropdown.classList.add("hidden");
    }
  });
}

function selectTicker(thscode, name) {
  document.getElementById("inputTicker").value = `${thscode} ${name}`;
  document.getElementById("tickerCandidatesDropdown").classList.add("hidden");
}

function getParsedTicker() {
  const raw = document.getElementById("inputTicker").value.trim();
  const match = raw.match(/([0-9]{6}\.[A-Z]{2})/);
  if (match) return match[1];
  return raw.split(" ")[0];
}

// 3. Step 0 命题澄清与合规改写（严禁跳步，唯一准入入口）
async function handleClarify() {
  const ticker = getParsedTicker();
  const hypothesis = document.getElementById("inputHypothesis").value.trim();
  const startDate = document.getElementById("inputStartDate").value;
  const endDate = document.getElementById("inputEndDate").value;

  if (!ticker || !hypothesis) {
    alert("请完整填写标的代码与待验证的投资命题！");
    return;
  }

  const btn = document.getElementById("btnClarify");
  btn.disabled = true;
  btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> 正在进行合规因果澄清...`;

  try {
    const res = await fetch("/api/clarify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ticker: ticker,
        hypothesis: hypothesis,
        analysis_start_date: startDate,
        analysis_end_date: endDate
      })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "澄清请求失败");
    }
    const data = await res.json();

    document.getElementById("editableClarifiedHypothesis").value = data.clarified_hypothesis;
    if (data.analysis_start_date) {
      document.getElementById("inputStartDate").value = data.analysis_start_date;
    }
    if (data.analysis_end_date) {
      document.getElementById("inputEndDate").value = data.analysis_end_date;
    }
    const periodDatesText = document.getElementById("clarifyPeriodDatesText");
    if (periodDatesText) {
      periodDatesText.innerText = `${data.analysis_start_date || startDate} 至 ${data.analysis_end_date || endDate}`;
    }

    document.getElementById("complianceNoteText").innerHTML = `<strong><i class="fa-solid fa-info-circle"></i> 合规审查提示:</strong> ${escapeHtml(data.compliance_notes)}`;
    
    const dimsContainer = document.getElementById("suggestedDimsTags");
    dimsContainer.innerHTML = (data.suggested_dimensions || []).map(d => `<span class="tag">${escapeHtml(d)}</span>`).join("");

    document.getElementById("clarifyResultBox").classList.remove("hidden");
    document.getElementById("clarifyResultBox").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    alert("澄清失败: " + e.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<i class="fa-solid fa-wand-magic-sparkles"></i> 智能合规澄清与边界界定`;
  }
}

// 4. 用户编辑确认后启动验证流水线（不可绕过）
async function confirmClarifiedAndVerify() {
  const confirmedHypothesis = document.getElementById("editableClarifiedHypothesis").value.trim();
  if (!confirmedHypothesis) {
    alert("澄清后的命题文本不能为空！");
    return;
  }

  const ticker = getParsedTicker();
  const startDate = document.getElementById("inputStartDate").value;
  const endDate = document.getElementById("inputEndDate").value;

  try {
    const res = await fetch("/api/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ticker: ticker,
        clarified_hypothesis: confirmedHypothesis,
        hypothesis: confirmedHypothesis,
        analysis_start_date: startDate,
        analysis_end_date: endDate
      })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "创建验证任务失败");
    }
    const data = await res.json();
    currentTaskId = data.task_id;

    document.getElementById("currentTaskIdLabel").innerText = `任务 ID: ${currentTaskId}`;
    document.getElementById("pipelineErrorBanner").classList.add("hidden");
    document.getElementById("resultsContainer").classList.add("hidden");
    document.getElementById("pipelineProgressPanel").scrollIntoView({ behavior: "smooth" });

    // 启动状态轮询
    startPolling(currentTaskId);
  } catch (e) {
    alert("启动流水线失败: " + e.message);
  }
}

// 5. 状态机轮询与步进器
function startPolling(taskId) {
  if (pollTimer) clearInterval(pollTimer);
  updateStepperState("CREATED");

  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/tasks/${taskId}`);
      if (!res.ok) return;
      const task = await res.json();
      currentTaskData = task;

      updateStepperState(task.status);
      const statusBadge = document.getElementById("taskStatusBadge");
      statusBadge.innerText = task.status;
      if (task.status === "COMPLETED") {
        statusBadge.className = "badge badge-success";
      } else if (task.status === "PARTIALLY_COMPLETED") {
        statusBadge.className = "badge badge-warning";
      } else if (task.status === "FAILED") {
        statusBadge.className = "badge badge-danger";
      } else if (task.status === "REPLENISHING") {
        statusBadge.className = "badge badge-warning";
      } else {
        statusBadge.className = "badge badge-info";
      }

      if (["COMPLETED", "PARTIALLY_COMPLETED", "FAILED"].includes(task.status)) {
        clearInterval(pollTimer);
        renderTaskResults(task);
      }
    } catch (e) {
      console.error("轮询异常:", e);
    }
  }, 1000);
}

const STEP_ORDER = [
  "CREATED",
  "DECOMPOSING",
  "RETRIEVING",
  "EXTRACTING_AND_COMPUTING",
  "SUFFICIENCY_CHECKING",
  "REPLENISHING",
  "CLASSIFYING",
  "VALIDATING",
  "SYNTHESIZING",
  "COMPLETED"
];

function updateStepperState(status) {
  const currentIdx = STEP_ORDER.indexOf(status);
  STEP_ORDER.forEach((step, idx) => {
    const node = document.getElementById(`stepNode-${step}`);
    if (!node) return;
    node.classList.remove("active", "completed");

    if (idx < currentIdx || status === "COMPLETED" || status === "PARTIALLY_COMPLETED") {
      node.classList.add("completed");
    } else if (idx === currentIdx) {
      node.classList.add("active");
    }
  });
}

// 6. 渲染完整研报与多证据看板
function renderTaskResults(task) {
  const errBanner = document.getElementById("pipelineErrorBanner");
  const errText = document.getElementById("pipelineErrorText");

  if (task.status === "FAILED") {
    errBanner.classList.remove("hidden");
    errText.textContent = task.error_message || "任务在执行阶段质检门禁未通过，或核心数据源不可用。";
    document.getElementById("resultsContainer").classList.add("hidden");
    errBanner.scrollIntoView({ behavior: "smooth" });
    return;
  } else {
    errBanner.classList.add("hidden");
  }

  const report = task.report;
  if (!report) {
    if (task.error_message) {
      errBanner.classList.remove("hidden");
      errText.textContent = task.error_message;
    }
    return;
  }

  document.getElementById("resultsContainer").classList.remove("hidden");
  document.getElementById("resultsContainer").scrollIntoView({ behavior: "smooth" });

  // 总结论看板
  const vTag = document.getElementById("verdictStatusTag");
  vTag.innerText = report.verdict_status;
  vTag.setAttribute("data-tooltip", TOOLTIP_TEXTS[report.verdict_status] || "研报总结论判定");
  if (report.verdict_status === "SUPPORTED") {
    vTag.style.backgroundColor = "var(--success)";
  } else if (report.verdict_status === "PARTIALLY_SUPPORTED") {
    vTag.style.backgroundColor = "var(--warning)";
    vTag.style.color = "#000";
  } else if (report.verdict_status === "REFUTED") {
    vTag.style.backgroundColor = "var(--danger)";
    vTag.style.color = "#fff";
  } else {
    vTag.style.backgroundColor = "var(--secondary)";
    vTag.style.color = "#fff";
  }

  const cTag = document.getElementById("confidenceLevelTag");
  cTag.innerText = `${report.confidence_level} 置信度`;
  cTag.setAttribute("data-tooltip", TOOLTIP_TEXTS[report.confidence_level] || "证据链整体置信度");
  document.getElementById("verdictHeadlineText").innerText = report.verdict_headline;
  document.getElementById("complianceDisclaimerText").innerText = report.compliance_disclaimer;

  // 三相事实与推论摘要
  document.getElementById("factsList").innerHTML = (report.facts_summary || []).map(f => `<li>${escapeHtml(f)}</li>`).join("");
  document.getElementById("inferencesList").innerHTML = (report.inferences_summary || []).map(f => `<li>${escapeHtml(f)}</li>`).join("");
  document.getElementById("uncertaintiesList").innerHTML = (report.uncertainties_summary || []).map(f => `<li>${escapeHtml(f)}</li>`).join("");

  // 子假设分项独立判定概览看板
  const subGrid = document.getElementById("subHypothesesGrid");
  if (subGrid && task.sub_hypotheses) {
    subGrid.innerHTML = task.sub_hypotheses.map(sh => {
      const vClass = (sh.verdict_status || "unverifiable").toLowerCase();
      const supCount = (sh.supported_evidence_ids || []).length;
      const agCount = (sh.against_evidence_ids || []).length;
      const unvCount = (sh.unverifiable_evidence_ids || []).length;
      const vTip = TOOLTIP_TEXTS[sh.verdict_status] || "子假设判定状态";
      const cTip = TOOLTIP_TEXTS[sh.confidence_level] || "子假设置信度";
      return `
        <div class="sub-hypothesis-card">
          <div class="sub-card-header">
            <span class="sub-id-badge">${escapeHtml(sh.id)}</span>
            <span class="sub-dim-label"><i class="fa-solid fa-cube"></i> ${escapeHtml(sh.dimension)}</span>
          </div>
          <div class="sub-statement-text">${escapeHtml(sh.statement)}</div>
          <div class="sub-verdict-row">
            <span class="sub-badge ${vClass}" data-tooltip="${escapeHtml(vTip)}">${escapeHtml(sh.verdict_status || "UNVERIFIABLE")} <i class="fa-solid fa-circle-info" style="font-size:10px;margin-left:3px;"></i></span>
            <span class="badge badge-info" data-tooltip="${escapeHtml(cTip)}">${escapeHtml(sh.confidence_level || "LOW")} 置信度 <i class="fa-solid fa-circle-info" style="font-size:10px;margin-left:3px;"></i></span>
          </div>
          <div class="sub-evidence-meta">
            <span><i class="fa-solid fa-thumbs-up"></i> 支持: ${supCount}</span>
            <span><i class="fa-solid fa-thumbs-down"></i> 反对: ${agCount}</span>
            <span><i class="fa-solid fa-circle-question"></i> 缺口: ${unvCount}</span>
          </div>
          ${sh.evidence_gap_note ? `<div class="sub-gap-note"><i class="fa-solid fa-triangle-exclamation"></i> ${escapeHtml(sh.evidence_gap_note)}</div>` : ''}
        </div>
      `;
    }).join("");
  }

  // 反转触发条件看板
  const falsGrid = document.getElementById("falsificationGrid");
  falsGrid.innerHTML = (report.falsification_triggers || []).map(trig => {
    const isHist = trig.trigger_type === "HISTORICAL_ATTRIBUTION";
    const typeLabel = isHist ? "历史归因推翻条件" : "未来持续性失效条件";
    const cardClass = isHist ? "history" : "future";
    const trigTip = isHist ? TOOLTIP_TEXTS.HISTORICAL_ATTRIBUTION : TOOLTIP_TEXTS.FUTURE_SUSTAINABILITY;
    return `
      <div class="falsification-card ${cardClass}">
        <span class="trigger-badge" data-tooltip="${escapeHtml(trigTip)}">${typeLabel} <i class="fa-solid fa-circle-info" style="font-size:10px;margin-left:4px;"></i></span>
        <div class="trigger-desc">${escapeHtml(trig.condition_description)}</div>
        <div class="trigger-basis"><strong>分析依据:</strong> ${escapeHtml(trig.analytical_basis)}</div>
      </div>
    `;
  }).join("");

  // 三栏多证据看板
  renderEvidenceColumns(task.evidence_items || []);

  // 渲染已有追问记录
  renderInquiryHistory(task.inquiries || []);
}

function renderEvidenceColumns(evidences) {
  const supportList = [];
  const againstList = [];
  const unverifiableList = [];

  evidences.forEach(ev => {
    if (ev.polarity === "SUPPORT") supportList.push(ev);
    else if (ev.polarity === "AGAINST") againstList.push(ev);
    else unverifiableList.push(ev);
  });

  document.getElementById("supportCount").innerText = supportList.length;
  document.getElementById("againstCount").innerText = againstList.length;
  document.getElementById("unverifiableCount").innerText = unverifiableList.length;

  const renderCard = (ev) => `
    <div class="evidence-card" onclick="openEvidenceDrawer('${escapeHtml(ev.evidence_id)}')">
      <div class="card-top">
        <span class="evidence-id-tag">${escapeHtml(ev.evidence_id)}</span>
        <span style="font-size:10px;color:var(--text-dim);">${escapeHtml(ev.source_type)}</span>
      </div>
      <div class="evidence-title">${escapeHtml(ev.title)}</div>
      <div class="evidence-summary">${escapeHtml(ev.summary)}</div>
      ${ev.exact_quote ? `<div class="evidence-quote-snippet">"${escapeHtml(ev.exact_quote)}"</div>` : ''}
      ${ev.weaked_sub_aspect ? `<div style="font-size:11px;color:var(--danger);margin-top:4px;"><strong>削弱维度:</strong> ${escapeHtml(ev.weaked_sub_aspect)}</div>` : ''}
      ${ev.unverifiable_reason ? `<div style="font-size:11px;color:var(--warning);margin-top:4px;"><strong>缺失说明:</strong> ${escapeHtml(ev.unverifiable_reason)}</div>` : ''}
    </div>
  `;

  document.getElementById("supportCardsList").innerHTML = supportList.map(renderCard).join("");
  document.getElementById("againstCardsList").innerHTML = againstList.map(renderCard).join("");
  document.getElementById("unverifiableCardsList").innerHTML = unverifiableList.map(renderCard).join("");
}

// 7. 证据穿透溯源抽屉
function openEvidenceDrawer(evidenceId) {
  if (!currentTaskData) return;
  const ev = (currentTaskData.evidence_items || []).find(e => e.evidence_id === evidenceId);
  if (!ev) return;

  document.getElementById("drawerTitle").innerText = `证据穿透溯源: ${ev.evidence_id}`;

  const comp = currentTaskData.computed_metrics ? currentTaskData.computed_metrics.find(c => c.computation_id === ev.computation_id) : null;
  const linkedFacts = currentTaskData.extracted_facts ? currentTaskData.extracted_facts.filter(f => ev.fact_ids.includes(f.fact_id)) : [];
  const linkedRaws = currentTaskData.raw_records ? currentTaskData.raw_records.filter(r => ev.inherited_source_ids.includes(r.raw_ref_id)) : [];

  let html = `
    <div class="trace-node" style="border-left: 4px solid var(--primary);">
      <h5><i class="fa-solid fa-id-card"></i> 证据卡片节点 (${escapeHtml(ev.evidence_id)})</h5>
      <p style="font-size:13px;font-weight:600;margin-bottom:4px;">${escapeHtml(ev.title)}</p>
      <p style="font-size:12px;color:var(--text-muted);">${escapeHtml(ev.summary)}</p>
    </div>
  `;

  if (comp) {
    html += `
      <div class="trace-node" style="border-left: 4px solid var(--warning);">
        <h5><i class="fa-solid fa-calculator"></i> 确定性计算节点 (${escapeHtml(comp.computation_id)})</h5>
        <p style="font-size:12px;"><strong>指标名称:</strong> ${escapeHtml(comp.name)}</p>
        <p style="font-size:12px;"><strong>计算结果:</strong> ${escapeHtml(comp.value)} ${escapeHtml(comp.unit)} (${escapeHtml(comp.scope)})</p>
        <p style="font-size:12px;"><strong>数学公式:</strong> <code>${escapeHtml(comp.formula)}</code></p>
        <p style="font-size:12px;color:var(--info);"><strong>代入路径:</strong> <code>${escapeHtml(comp.trace)}</code></p>
      </div>
    `;
  }

  if (linkedFacts.length > 0) {
    html += `
      <div class="trace-node" style="border-left: 4px solid var(--success);">
        <h5><i class="fa-solid fa-bullseye"></i> 原子事实抽取节点 (${linkedFacts.length} 项)</h5>
        ${linkedFacts.map(f => `
          <div style="margin-bottom:8px;padding-bottom:8px;border-bottom:1px dashed var(--card-border);">
            <div style="font-size:12px;font-weight:600;">[${escapeHtml(f.fact_id)}] ${escapeHtml(f.period)} ${escapeHtml(f.metric_name)}: ${escapeHtml(f.numeric_value)} ${escapeHtml(f.unit)}</div>
            <div style="font-size:11px;background:rgba(0,0,0,0.3);padding:6px;border-radius:4px;margin-top:4px;">
              <strong>严格保留标点引文:</strong> "${escapeHtml(f.exact_quote)}"
            </div>
            <div style="font-size:10px;color:var(--success);margin-top:2px;">
              <i class="fa-solid fa-check"></i> 原文子串与数值一致性核验通过 (verified_in_raw=true)
            </div>
          </div>
        `).join("")}
      </div>
    `;
  }

  if (linkedRaws.length > 0) {
    html += `
      <div class="trace-node" style="border-left: 4px solid var(--info);">
        <h5><i class="fa-solid fa-file-invoice"></i> 底层原始真实材料 (${linkedRaws.length} 份)</h5>
        ${linkedRaws.map(r => `
          <div style="margin-bottom:8px;">
            <div style="font-size:12px;font-weight:600;">[${escapeHtml(r.raw_ref_id)}] ${escapeHtml(r.title)} (${escapeHtml(r.publish_date || '未知日期')})</div>
            <div style="font-size:11px;color:var(--text-muted);margin-bottom:4px;">来源类型: ${escapeHtml(r.source_type)}</div>
            <pre style="white-space:pre-wrap;font-size:11px;background:#000;padding:8px;border-radius:4px;max-height:150px;overflow-y:auto;color:#cbd5e1;">${escapeHtml(r.raw_text)}</pre>
          </div>
        `).join("")}
      </div>
    `;
  }

  document.getElementById("drawerBody").innerHTML = html;
  document.getElementById("evidenceDrawerOverlay").classList.remove("hidden");
  document.getElementById("evidenceDrawer").classList.remove("hidden");
}

function closeDrawer() {
  document.getElementById("evidenceDrawerOverlay").classList.add("hidden");
  document.getElementById("evidenceDrawer").classList.add("hidden");
}

// 8. 闭环追问
async function handleSendInquiry() {
  if (!currentTaskId) {
    alert("请先运行完成一个研究任务！");
    return;
  }
  const input = document.getElementById("inputInquiryQuestion");
  const q = input.value.trim();
  if (!q) return;

  const btn = document.getElementById("btnSendInquiry");
  btn.disabled = true;
  btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i>`;

  try {
    const res = await fetch(`/api/tasks/${currentTaskId}/inquire`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "追问失败");
    }
    input.value = "";
    
    const tRes = await fetch(`/api/tasks/${currentTaskId}`);
    currentTaskData = await tRes.json();
    renderInquiryHistory(currentTaskData.inquiries || []);
  } catch (e) {
    alert("追问失败: " + e.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<i class="fa-solid fa-paper-plane"></i> 提问`;
  }
}

function renderInquiryHistory(inquiries) {
  const container = document.getElementById("inquiryHistory");
  if (!inquiries || inquiries.length === 0) {
    container.innerHTML = `<div style="font-size:12px;color:var(--text-dim);text-align:center;padding:12px;">暂无追问记录，可在下方输入框针对已存证据继续提问。</div>`;
    return;
  }

  container.innerHTML = inquiries.map(inq => `
    <div class="inquiry-item">
      <div class="inquiry-q"><i class="fa-solid fa-circle-question"></i> ${escapeHtml(inq.question)}</div>
      <div class="inquiry-a">${escapeHtml(inq.answer)}</div>
      <div class="inquiry-cites">
        <i class="fa-solid fa-link"></i> 引用已存证据: ${(inq.cited_evidence_ids || []).map(id => `<span class="evidence-id-tag">${escapeHtml(id)}</span>`).join(" ")}
      </div>
    </div>
  `).join("");
}

// 9. 历史任务模态框
async function openHistoryModal() {
  document.getElementById("historyModal").classList.remove("hidden");
  loadTaskList();
}

function closeHistoryModal() {
  document.getElementById("historyModal").classList.add("hidden");
}

async function loadTaskList() {
  const starredOnly = document.getElementById("chkStarredOnly").checked;
  try {
    const res = await fetch(`/api/tasks?starred_only=${starredOnly}`);
    const tasks = await res.json();

    const tbody = document.getElementById("taskTableBody");
    tbody.innerHTML = tasks.map(t => `
      <tr>
        <td>
          <button class="btn-demo" onclick="toggleStar('${escapeHtml(t.task_id)}', ${!t.starred})">
            <i class="fa-${t.starred ? 'solid' : 'regular'} fa-star" style="color:${t.starred ? '#f59e0b' : 'inherit'};"></i>
          </button>
        </td>
        <td><strong>${escapeHtml(t.ticker_name)}</strong> (${escapeHtml(t.ticker)})</td>
        <td style="max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(t.hypothesis)}</td>
        <td><span class="badge">${escapeHtml(t.verdict_status || t.status)}</span></td>
        <td>${escapeHtml(t.confidence_level || '--')}</td>
        <td style="font-size:11px;color:var(--text-dim);">${escapeHtml(t.created_at ? t.created_at.slice(0, 19).replace('T', ' ') : '')}</td>
        <td>
          <button class="btn-primary" style="padding:4px 8px;font-size:11px;" onclick="loadHistoricalTask('${escapeHtml(t.task_id)}')">查看</button>
        </td>
      </tr>
    `).join("");
  } catch (e) {
    console.error("加载任务列表失败:", e);
  }
}

async function toggleStar(taskId, newStarred) {
  try {
    await fetch(`/api/tasks/${taskId}/star`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ starred: newStarred })
    });
    loadTaskList();
  } catch (e) {
    console.error("更新收藏失败:", e);
  }
}

async function loadHistoricalTask(taskId) {
  closeHistoryModal();
  currentTaskId = taskId;
  document.getElementById("currentTaskIdLabel").innerText = `任务 ID: ${currentTaskId}`;
  startPolling(taskId);
}

// 10. 双任务并排对比模态框
async function openCompareModal() {
  document.getElementById("compareModal").classList.remove("hidden");
  try {
    const res = await fetch("/api/tasks");
    const tasks = await res.json();
    const selA = document.getElementById("selectTaskA");
    const selB = document.getElementById("selectTaskB");

    const options = tasks.map(t => `
      <option value="${escapeHtml(t.task_id)}">[${escapeHtml(t.ticker_name)}] ${escapeHtml(t.hypothesis.slice(0, 30))}... (${escapeHtml(t.task_id)})</option>
    `).join("");

    selA.innerHTML = options;
    selB.innerHTML = options;
    if (tasks.length > 1) {
      selB.selectedIndex = 1;
    }
  } catch (e) {
    console.error("加载对比任务选择器失败:", e);
  }
}

function closeCompareModal() {
  document.getElementById("compareModal").classList.add("hidden");
}

async function handleExecuteCompare() {
  const taskA = document.getElementById("selectTaskA").value;
  const taskB = document.getElementById("selectTaskB").value;
  if (!taskA || !taskB || taskA === taskB) {
    alert("请选择两个不同的任务进行对比！");
    return;
  }

  try {
    const res = await fetch(`/api/compare?task_a=${encodeURIComponent(taskA)}&task_b=${encodeURIComponent(taskB)}`);
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "对比接口调用失败");
    }
    const data = await res.json();
    renderCompareResult(data);
  } catch (e) {
    alert("对比失败: " + e.message);
  }
}

function renderCompareResult(data) {
  const tA = data.task_a;
  const tB = data.task_b;
  const s = data.comparison_summary;

  const view = document.getElementById("compareResultView");
  view.innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:16px;">
      <div class="panel" style="padding:14px;">
        <h4>${escapeHtml(s.task_a_ticker)}</h4>
        <p style="font-size:12px;color:var(--text-muted);margin:6px 0;"><strong>命题:</strong> ${escapeHtml(tA.clarified_hypothesis)}</p>
        <p style="font-size:13px;"><strong>核心结论:</strong> <span class="badge badge-success">${escapeHtml(s.task_a_verdict)}</span> (${escapeHtml(s.task_a_confidence)})</p>
        <p style="font-size:12px;margin-top:6px;"><strong>支持证据:</strong> ${tA.evidence_items.filter(e => e.polarity === 'SUPPORT').length} 项</p>
        <p style="font-size:12px;"><strong>反对证据:</strong> ${tA.evidence_items.filter(e => e.polarity === 'AGAINST').length} 项</p>
      </div>
      <div class="panel" style="padding:14px;">
        <h4>${escapeHtml(s.task_b_ticker)}</h4>
        <p style="font-size:12px;color:var(--text-muted);margin:6px 0;"><strong>命题:</strong> ${escapeHtml(tB.clarified_hypothesis)}</p>
        <p style="font-size:13px;"><strong>核心结论:</strong> <span class="badge badge-success">${escapeHtml(s.task_b_verdict)}</span> (${escapeHtml(s.task_b_confidence)})</p>
        <p style="font-size:12px;margin-top:6px;"><strong>支持证据:</strong> ${tB.evidence_items.filter(e => e.polarity === 'SUPPORT').length} 项</p>
        <p style="font-size:12px;"><strong>反对证据:</strong> ${tB.evidence_items.filter(e => e.polarity === 'AGAINST').length} 项</p>
      </div>
    </div>
    <div class="compliance-note" style="margin-top:14px;">
      <strong>对比总结:</strong> ${escapeHtml(s.comparison_note)} (同标的对比: ${s.is_same_ticker ? '是' : '否'})
    </div>
  `;
  view.classList.remove("hidden");
}
