const storageKey = "novel-reader-web-state-v2";

const state = {
  books: [],
  currentBook: null,
  csrfToken: "",
  documents: [],
  currentDocument: null,
  documentView: "preview",
  currentSessionId: "",
  readingStatus: null,
  lastResult: null,
};

const taskState = {
  active: null,
  history: [],
};

const autoSurveyState = {
  status: "stopped",
  requestedPause: false,
  currentChapter: null,
  completed: 0,
  lastError: null,
  usage: {
    inputTokens: 0,
    outputTokens: 0,
    cacheReadTokens: 0,
    cacheCreationTokens: 0,
  },
};

const autoSurveyPromptBudget = 18000;
const targetCacheHitRate = 0.6;
const maxCacheWarmupsPerChapter = 2;

const $ = (id) => document.getElementById(id);

function nowTime() {
  return new Date().toLocaleTimeString();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function saveLocalState() {
  const perBookSession = JSON.parse(localStorage.getItem(`${storageKey}:sessions`) || "{}");
  if (state.currentBook?.book_id && state.currentSessionId) {
    perBookSession[state.currentBook.book_id] = state.currentSessionId;
  }
  localStorage.setItem(`${storageKey}:sessions`, JSON.stringify(perBookSession));
  localStorage.setItem(storageKey, JSON.stringify({
    selectedBookId: state.currentBook?.book_id || "",
    currentSessionId: state.currentSessionId || "",
    autoSurvey: {
      status: autoSurveyState.status === "paused" ? "paused" : "stopped",
      sessionId: state.currentSessionId || "",
      currentChapter: autoSurveyState.currentChapter,
      completed: autoSurveyState.completed,
    },
    lastPredictForm: readPredictForm(false),
    lastContinueForm: readContinueForm(false),
    lastSearchQuery: $("searchQuery")?.value || "",
  }));
}

function restoreLocalState() {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || "{}");
    if (saved.lastPredictForm) writePredictForm(saved.lastPredictForm);
    if (saved.lastContinueForm) writeContinueForm(saved.lastContinueForm);
    if (saved.lastSearchQuery && $("searchQuery")) $("searchQuery").value = saved.lastSearchQuery;
    if (saved.autoSurvey) {
      autoSurveyState.status = saved.autoSurvey.status === "paused" ? "paused" : "stopped";
      autoSurveyState.currentChapter = saved.autoSurvey.currentChapter || null;
      autoSurveyState.completed = Number(saved.autoSurvey.completed || 0);
    }
    return saved;
  } catch {
    return {};
  }
}

function sessionForBook(bookId) {
  const sessions = JSON.parse(localStorage.getItem(`${storageKey}:sessions`) || "{}");
  return sessions[bookId] || "";
}

function showToast(message) {
  const toast = $("toast");
  toast.textContent = message;
  toast.hidden = false;
  setTimeout(() => { toast.hidden = true; }, 3600);
}

function normalizeError(error) {
  if (error?.payload?.error) return error.payload.error;
  if (error?.payload) return error.payload;
  if (typeof error === "object" && error?.message) return { message: error.message };
  return { message: String(error) };
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = { ...(options.headers || {}) };
  if (method !== "GET") {
    if (!state.csrfToken) {
      await ensureCsrfToken();
    }
    headers["Content-Type"] = "application/json";
    if (state.csrfToken) headers["X-Novel-Reader-Token"] = state.csrfToken;
  }
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({ ok: false, error: "响应不是 JSON" }));
  if (!response.ok || data.ok === false) {
    const err = new Error(typeof data.error === "string" ? data.error : (data.error?.message || `请求失败：${response.status}`));
    err.payload = data;
    throw err;
  }
  return data;
}

async function ensureCsrfToken() {
  const response = await fetch("/api/health", { cache: "no-store" });
  const data = await response.json().catch(() => ({}));
  if (data.csrf_token) {
    state.csrfToken = data.csrf_token;
  }
  return state.csrfToken;
}

function requireBook() {
  if (!state.currentBook) {
    throw new Error("????????");
  }
  return state.currentBook.book_id;
}

function startTask(type, name) {
  const id = `${type}-${Date.now()}`;
  taskState.active = {
    id,
    name,
    type,
    status: "running",
    startedAt: Date.now(),
    endedAt: null,
    progress: { mode: "determinate", percent: 8, label: "???..." },
    steps: [{ time: nowTime(), level: "info", text: "?????" }],
    result: null,
    error: null,
  };
  renderActiveTask();
  return taskState.active;
}

function updateTaskProgress(percent, label, mode = "determinate") {
  if (!taskState.active) return;
  taskState.active.progress = { mode, percent: Math.max(0, Math.min(100, percent)), label };
  renderActiveTask();
}

function addTaskStep(level, text) {
  if (!taskState.active) return;
  taskState.active.steps.push({ time: nowTime(), level, text });
  renderActiveTask();
}

function finishTask(result) {
  if (!taskState.active) return;
  taskState.active.status = "success";
  taskState.active.endedAt = Date.now();
  taskState.active.progress = { mode: "determinate", percent: 100, label: "??" };
  taskState.active.result = result;
  taskState.active.steps.push({ time: nowTime(), level: "ok", text: "????" });
  taskState.history.unshift(taskState.active);
  taskState.history = taskState.history.slice(0, 8);
  renderActiveTask();
  renderTaskHistory();
}

function failTask(error) {
  if (!taskState.active) return;
  taskState.active.status = "error";
  taskState.active.endedAt = Date.now();
  taskState.active.error = normalizeError(error);
  taskState.active.steps.push({ time: nowTime(), level: "error", text: taskState.active.error.message || "????" });
  taskState.history.unshift(taskState.active);
  taskState.history = taskState.history.slice(0, 8);
  renderActiveTask();
  renderTaskHistory();
}

function elapsedText(task) {
  if (!task) return "0s";
  const end = task.endedAt || Date.now();
  return `${Math.max(0, Math.round((end - task.startedAt) / 1000))}s`;
}

function renderActiveTask() {
  const task = taskState.active;
  const badge = $("taskStatusBadge");
  if (!task) {
    $("taskSubtitle").textContent = "????????";
    badge.textContent = "??";
    badge.className = "badge neutral";
    $("taskProgressLabel").textContent = "????";
    $("taskElapsed").textContent = "0s";
    $("taskProgressFill").style.width = "0%";
    $("taskProgressFill").classList.remove("indeterminate");
    $("taskLog").innerHTML = "";
    return;
  }
  $("taskSubtitle").textContent = `${task.name} ? ${translateTaskType(task.type)}`;
  badge.textContent = translateTaskStatus(task.status);
  badge.className = `badge ${task.status === "success" ? "ok" : task.status === "error" ? "bad" : "info"}`;
  $("taskProgressLabel").textContent = task.progress.label;
  $("taskElapsed").textContent = elapsedText(task);
  $("taskProgressFill").style.width = task.progress.mode === "indeterminate" ? "45%" : `${task.progress.percent}%`;
  $("taskProgressFill").classList.toggle("indeterminate", task.progress.mode === "indeterminate");
  $("taskLog").innerHTML = task.steps.map((step) => (
    `<div class="task-step ${step.level}"><span>${escapeHtml(step.time)}</span><p>${escapeHtml(step.text)}</p></div>`
  )).join("");
}

function renderTaskHistory() {
  // Kept as a hook for later UI expansion; history is intentionally compact in v1.
}

function translateTaskStatus(status) {
  return {
    idle: "??",
    running: "???",
    success: "??",
    error: "??",
  }[status] || status;
}

function translateTaskType(type) {
  return {
    reading: "??",
    embedding: "????",
    ingest: "??",
    search: "??",
    ask: "??",
    style: "??",
    predict: "??",
    continue: "??",
    claude: "Claude",
  }[type] || type;
}

function translateDocumentCategory(category) {
  return {
    maps: "????",
    reports: "????",
    styles: "????",
    continuations: "?????",
    predictions: "????",
    summaries: "????",
  }[category] || category;
}

function translateAutoSurveyStatus(status) {
  return {
    stopped: "???",
    running: "???",
    paused: "???",
    pausing: "???",
    error: "??",
  }[status] || status;
}

function translateReportAction(action) {
  return {
    outline: "????",
    map: "????",
    analyze: "????",
  }[action] || action;
}

async function withButtonLoading(button, loadingText, asyncFn) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = loadingText;
  try {
    return await asyncFn();
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function jsonDetails(data) {
  return `<details class="json-details"><summary>???? JSON</summary><pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre></details>`;
}

function setResult(html) {
  $("resultPreview").innerHTML = html;
}

function card(title, body, kind = "") {
  return `<article class="result-card ${kind}"><h3>${escapeHtml(title)}</h3>${body}</article>`;
}

function renderEvidenceList(evidence = []) {
  if (!evidence.length) return `<div class="empty-state">???????</div>`;
  return `<div class="evidence-list">${evidence.map((item) => `
    <article class="evidence-card">
      <div class="evidence-head">
        <strong>${escapeHtml(item.chunk_id || item.source || "??")}</strong>
        <span>? ${escapeHtml(item.chapter ?? item.chapter_index ?? "--")} ?</span>
      </div>
      <p>${escapeHtml(item.reason || item.source || "")}</p>
      <blockquote>${escapeHtml(item.excerpt || item.snippet || item.text || "")}</blockquote>
    </article>
  `).join("")}</div>`;
}

function renderStatusSummary(data) {
  return card("????", `
    <div class="summary-grid">
      <div><span>??</span><strong>${escapeHtml(data.chapter_count ?? "--")}</strong></div>
      <div><span>???</span><strong>${escapeHtml(data.chunk_count ?? "--")}</strong></div>
      <div><span>????</span><strong>${escapeHtml(data.summary_coverage?.percent ?? data.summary_coverage_percent ?? 0)}%</strong></div>
      <div><span>????</span><strong>${escapeHtml(data.vector_backend || "sqlite_cosine")}</strong></div>
    </div>
    ${jsonDetails(data)}
  `, "success-card");
}

function probabilityClass(value) {
  return value === "high" ? "ok" : value === "medium" ? "warn" : "neutral";
}

function translateProbability(value) {
  return {
    high: "高概率",
    medium: "中概率",
    low: "低概率",
  }[value] || value || "未标注";
}

function translatePredictionType(value) {
  return {
    plot_direction: "剧情走向",
    character_arc: "人物弧光",
    foreshadowing_payoff: "伏笔回收",
    conflict: "冲突推进",
    ending: "结局可能",
  }[value] || value || "预测";
}

function renderPredictionPacket(packet) {
  const groups = { high: [], medium: [], low: [] };
  (packet.predictions || []).forEach((item) => groups[item.probability || "medium"].push(item));
  const renderPrediction = (item) => `
    <article class="prediction-card">
      <div class="prediction-head">
        <span class="badge ${probabilityClass(item.probability)}">${escapeHtml(translateProbability(item.probability))}</span>
        <strong>${escapeHtml(translatePredictionType(item.type))}</strong>
      </div>
      <p class="prediction-claim">${escapeHtml(item.claim)}</p>
      <div class="confidence-row"><span>置信度</span><div class="progress-track"><div class="progress-fill" style="width:${Math.round((item.confidence || 0) * 100)}%"></div></div><strong>${escapeHtml(item.confidence)}</strong></div>
      <div class="chip-row">${(item.supporting_evidence || []).map((id) => `<span class="chip">${escapeHtml(id)}</span>`).join("")}</div>
      <p class="risk">${escapeHtml(item.risk || "")}</p>
    </article>`;
  return `
    ${card("当前剧情状态", `
      <div class="summary-grid">
        <div><span>最新章节</span><strong>${escapeHtml(packet.current_state?.latest_chapter ?? "--")}</strong></div>
        <div><span>摘要覆盖</span><strong>${escapeHtml(packet.book?.summary_coverage_percent ?? 0)}%</strong></div>
        <div><span>预测范围</span><strong>${escapeHtml(packet.prediction_goal?.scope)}</strong></div>
        <div><span>预测跨度</span><strong>${escapeHtml(packet.prediction_goal?.horizon)}</strong></div>
      </div>
    `, "success-card")}
    ${card("高概率预测", groups.high.map(renderPrediction).join("") || `<div class="empty-state">暂无</div>`)}
    ${card("中概率预测", groups.medium.map(renderPrediction).join("") || `<div class="empty-state">暂无</div>`)}
    ${card("低概率/反转预测", groups.low.map(renderPrediction).join("") || `<div class="empty-state">暂无</div>`)}
    ${card("证据", renderEvidenceList(packet.evidence || []))}
    ${card("下一章观察清单", `<ul>${(packet.watchlist || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`)}
    ${(packet.warnings || []).length ? card("警告", `<ul>${packet.warnings.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`, "warning-card") : ""}
    ${jsonDetails(packet)}
  `;
}

function renderContinuationPacket(packet) {
  return `
    ${card("续写任务包", `<p>${escapeHtml(packet.continuation_goal?.outline || "已生成可交给智能体的续写任务包。")}</p>`, "success-card")}
    ${card("最近上下文", renderEvidenceList(packet.recent_context || []))}
    ${card("剧情证据", renderEvidenceList(packet.plot_evidence || []))}
    ${jsonDetails(packet)}
  `;
}

function renderStylePacket(packet) {
  return `
    ${card("风格画像", `<p>摘要覆盖：${escapeHtml(packet.summary_coverage_percent ?? 0)}%</p>${jsonDetails(packet.corpus_stats || {})}`, "success-card")}
    ${card("短引文证据", renderEvidenceList(packet.evidence || packet.scene_profiles?.flatMap((item) => item.evidence || []) || []))}
    ${jsonDetails(packet)}
  `;
}

function renderReportResult(data) {
  const docs = data.documents || [];
  return card("文档已生成", `
    ${docs.length ? `<ul>${docs.map((doc) => `<li><button class="link-button" data-doc-path="${escapeHtml(doc.path)}">${escapeHtml(doc.path)}</button></li>`).join("")}</ul>` : "<p>操作完成。</p>"}
    ${jsonDetails(data)}
  `, "success-card");
}

function renderError(error) {
  const err = normalizeError(error);
  const coverage = err.coverage || {};
  return `<article class="error-card">
    <h3>${escapeHtml(err.code || "操作失败")}</h3>
    <p>${escapeHtml(err.message || error.message || "未知错误")}</p>
    ${Object.keys(coverage).length ? `<div class="summary-grid">
      <div><span>L1</span><strong>${escapeHtml(coverage.l1_coverage_percent ?? 0)}%</strong></div>
      <div><span>L2</span><strong>${escapeHtml(coverage.l2_coverage_percent ?? 0)}%</strong></div>
      <div><span>L3</span><strong>${escapeHtml(coverage.l3_coverage_percent ?? 0)}%</strong></div>
      <div><span>session</span><strong>${escapeHtml(coverage.session_status || "--")}</strong></div>
    </div>` : ""}
    ${err.missing_chapters ? `<p>缺失章节：${escapeHtml(JSON.stringify(err.missing_chapters))}</p>` : ""}
    ${err.next_action ? `<p class="next-action">下一步：${escapeHtml(err.next_action)}</p>` : ""}
    <div class="button-row">
      <button id="recoverRefreshStatus">刷新状态</button>
      <button id="recoverReadNext">读取下一章</button>
      <button id="recoverFinalize">完成阅读</button>
      <button id="recoverPartial">切换为阶段性范围</button>
    </div>
  </article>`;
}

function bindRecoveryButtons() {
  $("recoverRefreshStatus")?.addEventListener("click", () => refreshReadingStatus().catch(showError));
  $("recoverReadNext")?.addEventListener("click", () => runReadNext($("readNextBtn")).catch(showError));
  $("recoverFinalize")?.addEventListener("click", () => finalizeReading($("finalizeBtn")).catch(showError));
  $("recoverPartial")?.addEventListener("click", () => {
    if ($("predictScopeMode")) $("predictScopeMode").value = "partial";
    showToast("已切换预测为阶段性范围。");
  });
}

function showError(error) {
  setResult(renderError(error));
  bindRecoveryButtons();
  showToast(normalizeError(error).message || String(error));
}

async function loadHealth() {
  const data = await api("/api/health");
  state.csrfToken = data.csrf_token || state.csrfToken;
  renderServiceStatus(data);
  return data;
}

function renderServiceStatus(data) {
  renderEmbeddingStatus(data.embedding || {});
  renderClaudeStatus(data.claude || {});
}

function renderEmbeddingStatus(emb) {
  const pill = $("embeddingPill");
  if (!emb.configured) {
    pill.textContent = "语义索引未配置";
    pill.className = "badge warn";
  } else if (emb.available) {
    pill.textContent = "语义索引可用";
    pill.className = "badge ok";
  } else {
    pill.textContent = "语义索引不可用";
    pill.className = "badge bad";
  }
}

function renderClaudeStatus(status) {
  const pill = $("claudePill");
  if (!status.enabled) {
    pill.textContent = "Claude 未启用";
    pill.className = "badge warn";
  } else if (status.available) {
    pill.textContent = status.permission === "dangerous" ? "Claude 危险模式" : "Claude 可用";
    pill.className = status.permission === "dangerous" ? "badge bad" : "badge ok";
  } else {
    pill.textContent = "Claude 未找到";
    pill.className = "badge bad";
  }
}

async function loadBooks(preferredBookId = "") {
  const data = await api("/api/books");
  state.books = data.books || [];
  if (!state.books.length) {
    state.currentBook = null;
    renderBooks();
    renderCurrentBook();
    return;
  }
  const selected = preferredBookId || state.currentBook?.book_id || restoreLocalState().selectedBookId;
  state.currentBook = state.books.find((book) => book.book_id === selected) || state.books[0];
  state.currentSessionId = sessionForBook(state.currentBook.book_id) || state.currentSessionId || "";
  renderBooks();
  renderCurrentBook();
  await Promise.allSettled([loadStatus(), loadDocuments(), refreshReadingStatus()]);
}

function renderBooks() {
  const list = $("bookList");
  if (!state.books.length) {
    list.innerHTML = `<div class="empty-state">暂无书籍。先导入 TXT/Markdown。</div>`;
    return;
  }
  list.innerHTML = state.books.map((book) => {
    const active = state.currentBook?.book_id === book.book_id ? " active" : "";
    const coverage = book.summary_coverage?.percent ?? 0;
    return `<button class="book-item${active}" data-book="${escapeHtml(book.book_id)}">
      <strong>${escapeHtml(book.title)}</strong>
      <span>${escapeHtml(book.book_id)} · ${book.chapter_count} 章 · 摘要 ${coverage}%</span>
    </button>`;
  }).join("");
  list.querySelectorAll(".book-item").forEach((button) => {
    button.addEventListener("click", async () => {
      state.currentBook = state.books.find((book) => book.book_id === button.dataset.book);
      state.currentSessionId = sessionForBook(state.currentBook.book_id);
      saveLocalState();
      renderBooks();
      renderCurrentBook();
      await Promise.allSettled([loadStatus(), loadDocuments(), refreshReadingStatus()]);
    });
  });
}

function renderCurrentBook() {
  if (!state.currentBook) {
    $("currentTitle").textContent = "未选择书籍";
    $("bookMeta").textContent = "导入一本 TXT/Markdown 后开始。";
    $("selectedBookPill").textContent = "未选择书籍";
    return;
  }
  $("currentTitle").textContent = state.currentBook.title;
  $("bookMeta").textContent = `${state.currentBook.book_id} · ${state.currentBook.chapter_count} 章 · ${state.currentBook.chunk_count} 个文本块`;
  $("selectedBookPill").textContent = state.currentBook.title;
  $("selectedBookPill").className = "badge info";
  if ($("predictSessionId")) $("predictSessionId").value = state.currentSessionId || "";
}

async function loadStatus() {
  const book = requireBook();
  const data = await api(`/api/status/${encodeURIComponent(book)}`);
  setResult(renderStatusSummary(data));
  return data;
}

async function createReadingSession(mode, button) {
  await withButtonLoading(button, "创建中...", async () => {
    startTask("reading", `创建${translateReadingMode(mode)}会话`);
    updateTaskProgress(25, "发送创建请求");
    const data = await api("/api/reading/session", {
      method: "POST",
      body: JSON.stringify({ book: requireBook(), mode, goal: "full", deep_ratio: 0.25 }),
    });
    state.currentSessionId = data.session_id;
    if ($("predictSessionId")) $("predictSessionId").value = data.session_id;
    addTaskStep("ok", `阅读会话 ID：${data.session_id}`);
    updateTaskProgress(85, "刷新阅读状态");
    await refreshReadingStatus();
    saveLocalState();
    finishTask(data);
    setResult(card("阅读会话已创建", `<p>${escapeHtml(data.session_id)}</p>${jsonDetails(data)}`, "success-card"));
  });
}

function translateReadingMode(mode) {
  return {
    survey: "快速覆盖",
    balanced: "均衡阅读",
    deep: "深度阅读",
  }[mode] || mode;
}

function translateSessionStatus(status) {
  return {
    active: "进行中",
    finalized: "已完成",
    ready_to_finalize: "可完成",
  }[status] || status;
}

async function refreshReadingStatus() {
  if (!state.currentSessionId) {
    renderReadingProgress(null);
    return null;
  }
  const data = await api(`/api/reading/session/${encodeURIComponent(state.currentSessionId)}/status`);
  state.readingStatus = data;
  renderReadingProgress(data);
  return data;
}

function renderReadingProgress(status) {
  if (!status) {
    $("sessionMeta").textContent = "未创建阅读会话";
    setProgress("l1Progress", "l1ProgressText", 0);
    setProgress("l2Progress", "l2ProgressText", 0);
    setProgress("l3Progress", "l3ProgressText", 0);
    $("fullScopeState").textContent = "全书报告：锁定";
    $("fullScopeState").className = "scope-state locked";
    renderAutoSurveyState();
    return;
  }
  $("sessionMeta").textContent = `${status.session_id} · ${translateReadingMode(status.mode)} · ${translateSessionStatus(status.status)}`;
  setProgress("l1Progress", "l1ProgressText", status.l1_coverage_percent || 0);
  setProgress("l2Progress", "l2ProgressText", status.l2_coverage_percent || 0);
  setProgress("l3Progress", "l3ProgressText", status.l3_coverage_percent || 0);
  const complete = status.required_coverage_complete;
  const finalized = status.finalized;
  const allowed = status.full_scope_allowed;
  $("fullScopeState").textContent = allowed ? "全书报告：已解锁" : complete && !finalized ? "全书报告：覆盖已完成，请点击完成阅读" : "全书报告：锁定";
  $("fullScopeState").className = `scope-state ${allowed ? "allowed" : complete ? "ready" : "locked"}`;
  renderAutoSurveyState();
}

function setProgress(fillId, textId, percent) {
  const value = Math.max(0, Math.min(100, Number(percent) || 0));
  $(fillId).style.width = `${value}%`;
  $(textId).textContent = `${value}%`;
}

async function runReadNext(button) {
  if (!state.currentSessionId) throw new Error("请先创建阅读会话。");
  await withButtonLoading(button, "读取中...", async () => {
    startTask("reading", "读取下一章");
    updateTaskProgress(30, "读取下一批章节");
    const data = await api(`/api/reading/session/${encodeURIComponent(state.currentSessionId)}/next`, {
      method: "POST",
      body: JSON.stringify({ batch_chapters: 1 }),
    });
    finishTask(data);
    setResult(renderReadNext(data));
  });
}

function renderReadNext(data) {
  const chapters = data.chapters || [];
  return card("下一章阅读包", chapters.map((chapter) => `
    <section class="chapter-pack">
      <h4>第 ${escapeHtml(chapter.chapter)} 章 · ${escapeHtml(chapter.required_level)}</h4>
      ${renderEvidenceList(chapter.chunks || [])}
    </section>
  `).join("") + jsonDetails(data), "success-card");
}

async function submitNote(button) {
  if (!state.currentSessionId) throw new Error("请先创建阅读会话。");
  await withButtonLoading(button, "提交中...", async () => {
    startTask("reading", "提交章节笔记");
    updateTaskProgress(25, "校验笔记");
    const data = await api(`/api/reading/session/${encodeURIComponent(state.currentSessionId)}/submit-note`, {
      method: "POST",
      body: JSON.stringify({ chapter: Number($("noteChapter").value || 1), text: $("noteText").value }),
    });
    updateTaskProgress(80, "刷新进度");
    await refreshReadingStatus();
    finishTask(data);
    setResult(card("章节笔记已提交", jsonDetails(data), "success-card"));
  });
}

async function finalizeReading(button) {
  if (!state.currentSessionId) throw new Error("请先创建 reading session。");
  await withButtonLoading(button, "完成中...", async () => {
    startTask("reading", "完成阅读");
    updateTaskProgress(40, "检查覆盖率");
    const data = await api(`/api/reading/session/${encodeURIComponent(state.currentSessionId)}/finalize`, { method: "POST", body: JSON.stringify({}) });
    await refreshReadingStatus();
    finishTask(data);
    setResult(card("全书报告已解锁", jsonDetails(data), "success-card"));
  });
}

function renderAutoSurveyState() {
  const target = $("autoSurveyState");
  if (!target) return;
  const status = autoSurveyState.status;
  const session = state.currentSessionId ? `阅读会话 ${state.currentSessionId}` : "无阅读会话";
  const chapter = autoSurveyState.currentChapter ? ` · 当前第 ${autoSurveyState.currentChapter} 章` : "";
  const completed = autoSurveyState.completed ? ` · 本轮提交 ${autoSurveyState.completed} 章` : "";
  const coverage = state.readingStatus ? ` · L1 ${state.readingStatus.l1_coverage_percent || 0}%` : "";
  target.textContent = `${translateAutoSurveyStatus(status)} · ${session}${chapter}${completed}${coverage}`;
  target.className = `auto-survey-state ${status}`;
}

async function ensureClaudeBridgeReady() {
  const status = await api("/api/claude/status");
  if (!status.enabled) {
    throw new Error("Claude 网页桥接未启用。请用 .\\bin\\start-web.ps1 -EnableClaudeChat 重新启动 Web。");
  }
  if (!status.available) {
    throw new Error("未找到 claude 命令。请确认 Claude Code CLI 已安装并在 PATH 中。");
  }
  return status;
}

async function ensureSurveySession() {
  if (state.currentSessionId && state.readingStatus?.mode === "survey") {
    return state.currentSessionId;
  }
  addTaskStep("info", "当前没有快速覆盖阅读会话，自动创建。");
  const data = await api("/api/reading/session", {
    method: "POST",
    body: JSON.stringify({ book: requireBook(), mode: "survey", goal: "full", deep_ratio: 0.25 }),
  });
  state.currentSessionId = data.session_id;
  if ($("predictSessionId")) $("predictSessionId").value = data.session_id;
  await refreshReadingStatus();
  saveLocalState();
  addTaskStep("ok", `已创建快速覆盖阅读会话：${data.session_id}`);
  return data.session_id;
}

function buildL1NotePrompt(chapterPack) {
  const chunkIds = (chapterPack.chunks || []).map((chunk) => chunk.chunk_id);
  const chunks = chapterPack.chunks || [];
  const perChunkBudget = Math.max(800, Math.floor(autoSurveyPromptBudget / Math.max(chunks.length, 1)) - 120);
  const chunkText = chunks.map((chunk) => (
    `## ${chunk.chunk_id} · 第 ${chunk.chapter_index} 章 · 行 ${chunk.line_start}-${chunk.line_end}\n${clipTextForPrompt(chunk.text || "", perChunkBudget)}`
  )).join("\n\n");
  return [
    "请只根据下面提供的本章原文生成 Novel Reader L1_SKIMMED 笔记。",
    "如果本章原文被截断，请只概括已提供片段，并在 events 中标注“本章为自动 Survey 摘要，原文可能已截断”。",
    "不要输出解释、寒暄或 JSON，只输出可提交的 Markdown 笔记。",
    "必须包含这些字段标题：one_sentence、events、characters、evidence_chunks。",
    "笔记至少 100 个非空白字符。",
    `evidence_chunks 必须包含本章 chunk_id，优先列出全部：${chunkIds.join(", ")}`,
    "",
    `章节：第 ${chapterPack.chapter_index} 章`,
    `目标 level：${chapterPack.required_level}`,
    "",
    chunkText,
  ].join("\n");
}

function buildL2NotePrompt(chapterPack) {
  return buildStructuredNotePrompt(chapterPack, "L2_READ", [
    "事件",
    "人物与动机",
    "冲突",
    "情节因果",
    "伏笔/回收",
    "设定/地点/势力",
    "时间线",
    "写作观察",
    "证据块",
  ], 300);
}

function buildL3NotePrompt(chapterPack) {
  return buildStructuredNotePrompt(chapterPack, "L3_DEEP_READ", [
    "事件",
    "人物与动机",
    "冲突",
    "情节因果",
    "伏笔/回收",
    "设定/地点/势力",
    "时间线",
    "写作观察",
    "证据块",
    "scene_breakdown",
    "style_observation",
    "character_state",
    "continuity_constraints",
  ], 600);
}

function buildStructuredNotePrompt(chapterPack, level, fields, minChars) {
  const chunkIds = (chapterPack.chunks || []).map((chunk) => chunk.chunk_id);
  const chunks = chapterPack.chunks || [];
  const perChunkBudget = Math.max(800, Math.floor(autoSurveyPromptBudget / Math.max(chunks.length, 1)) - 120);
  const chunkText = chunks.map((chunk) => (
    `## ${chunk.chunk_id} · 第 ${chunk.chapter_index} 章 · 行 ${chunk.line_start}-${chunk.line_end}\n${clipTextForPrompt(chunk.text || "", perChunkBudget)}`
  )).join("\n\n");
  return [
    `请只根据下面提供的本章原文生成 Novel Reader ${level} 结构化阅读笔记。`,
    "不要输出解释、寒暄或 JSON；只输出可提交的 Markdown 笔记。",
    `笔记至少 ${minChars} 个非空白字符。`,
    `必须包含这些字段标题：${fields.join("、")}。`,
    `证据块/evidence_chunks 必须引用本章 chunk_id：${chunkIds.join(", ")}`,
    "如果原文被截断，只概括已提供片段，并在事件中注明自动阅读输入可能已截断。",
    "",
    `章节：第 ${chapterPack.chapter_index} 章`,
    `目标 level：${level}`,
    "",
    chunkText,
  ].join("\n");
}

function selectedAutoReadingLevel(requiredLevel) {
  const selected = $("autoReadingDepth")?.value || "auto";
  if (selected === "auto") return requiredLevel || "L1_SKIMMED";
  return selected;
}

function buildReadingNotePrompt(chapterPack) {
  const level = selectedAutoReadingLevel(chapterPack.required_level);
  if (level === "L3_DEEP_READ") return buildL3NotePrompt({ ...chapterPack, required_level: level });
  if (level === "L2_READ") return buildL2NotePrompt({ ...chapterPack, required_level: level });
  return buildL1NotePrompt({ ...chapterPack, required_level: "L1_SKIMMED" });
}

function clipTextForPrompt(text, maxChars) {
  const value = String(text || "");
  if (value.length <= maxChars) return value;
  const head = Math.floor(maxChars * 0.6);
  const tail = Math.max(0, maxChars - head - 80);
  return `${value.slice(0, head)}\n...[文本块内容已为一键快速覆盖截断]...\n${value.slice(-tail)}`;
}

function extractClaudeNoteText(data) {
  const candidates = [data?.reply, data?.text, data?.stdout, data?.output, data?.message];
  for (const value of candidates) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  if (Array.isArray(data?.messages)) {
    const text = data.messages.map((item) => item.text || item.content || "").filter(Boolean).join("\n").trim();
    if (text) return text;
  }
  return "";
}

function extractClaudeUsage(data) {
  const usage = data?.usage || data?.parsed?.usage || data?.raw_parsed?.usage || {};
  const cache = data?.cache || {};
  return {
    inputTokens: Number(usage.input_tokens || usage.inputTokens || 0),
    outputTokens: Number(usage.output_tokens || usage.outputTokens || 0),
    cacheReadTokens: Number(cache.read_input_tokens || usage.cache_read_input_tokens || usage.cache_read_tokens || 0),
    cacheCreationTokens: Number(cache.creation_input_tokens || usage.cache_creation_input_tokens || usage.cache_creation_tokens || 0),
    hitRate: typeof cache.hit_rate === "number" ? cache.hit_rate : null,
    available: Boolean(cache.available),
  };
}

function addClaudeUsage(data) {
  const usage = extractClaudeUsage(data);
  autoSurveyState.usage.inputTokens += usage.inputTokens;
  autoSurveyState.usage.outputTokens += usage.outputTokens;
  autoSurveyState.usage.cacheReadTokens += usage.cacheReadTokens;
  autoSurveyState.usage.cacheCreationTokens += usage.cacheCreationTokens;
  renderClaudeCacheStatus(usage);
  return usage;
}

function renderClaudeCacheStatus(latest = null) {
  const target = $("claudeCacheStatus");
  if (!target) return;
  const totalCache = autoSurveyState.usage.cacheReadTokens + autoSurveyState.usage.cacheCreationTokens;
  const cumulativeHitRate = totalCache ? autoSurveyState.usage.cacheReadTokens / totalCache : null;
  const latestLabel = latest?.available
    ? `本章命中率 ${(Number(latest.hitRate || 0) * 100).toFixed(1)}%`
    : "Claude 未返回缓存指标";
  const cumulativeLabel = cumulativeHitRate === null ? "--" : `${(cumulativeHitRate * 100).toFixed(1)}%`;
  target.innerHTML = `
    <div><strong>Claude 缓存</strong>：${escapeHtml(latestLabel)}</div>
    <div class="usage-summary">
      <span>输入 ${escapeHtml(autoSurveyState.usage.inputTokens)}</span>
      <span>输出 ${escapeHtml(autoSurveyState.usage.outputTokens)}</span>
      <span>cache read ${escapeHtml(autoSurveyState.usage.cacheReadTokens)}</span>
      <span>cache create ${escapeHtml(autoSurveyState.usage.cacheCreationTokens)}</span>
      <span>累计命中 ${escapeHtml(cumulativeLabel)}</span>
    </div>
  `;
}

async function warmClaudeCacheIfNeeded(chapter, firstUsage) {
  if (!firstUsage.available) {
    addTaskStep("warn", "Claude 未返回缓存指标，继续阅读。");
    return;
  }
  if ((firstUsage.hitRate || 0) >= targetCacheHitRate) return;
  for (let attempt = 1; attempt <= maxCacheWarmupsPerChapter; attempt += 1) {
    addTaskStep("warn", `缓存命中率偏低，执行第 ${attempt} 次有限预热。`);
    const warmup = await api("/api/claude/chat", {
      method: "POST",
      body: JSON.stringify({
        book: state.currentBook.book_id,
        message: [
          "缓存预热请求：请只回复 OK。",
          `reading_session_id=${state.currentSessionId}`,
          `chapter=${chapter.chapter_index}`,
          `required_level=${chapter.required_level}`,
        ].join("\n"),
        mode: "continue",
        context: { cache_warmup: true, reading_session_id: state.currentSessionId, chapter_index: chapter.chapter_index },
      }),
    });
    const warmupUsage = addClaudeUsage(warmup);
    if (warmupUsage.available && (warmupUsage.hitRate || 0) >= targetCacheHitRate) {
      addTaskStep("ok", "缓存预热后命中率已接近目标。");
      return;
    }
  }
  addTaskStep("warn", "缓存预热已达到上限，继续本章阅读，避免重复调用失控。");
}

function renderAutoSurveyResult(status, lastSubmit = null) {
  const total = status?.total_chapters ?? "--";
  const done = status?.completed_chapters ?? "--";
  const next = status?.current_chapter ?? "--";
  return card("一键快速覆盖阅读", `
    <div class="summary-grid">
      <div><span>已完成</span><strong>${escapeHtml(done)} / ${escapeHtml(total)}</strong></div>
      <div><span>L1 覆盖</span><strong>${escapeHtml(status?.l1_coverage_percent ?? 0)}%</strong></div>
      <div><span>当前章节</span><strong>${escapeHtml(next)}</strong></div>
      <div><span>状态</span><strong>${escapeHtml(status?.required_coverage_complete ? "覆盖完成，等待完成阅读" : "进行中")}</strong></div>
    </div>
    ${lastSubmit ? `<p>最近提交：第 ${escapeHtml(lastSubmit.chapter ?? lastSubmit.chapter_index ?? "--")} 章</p>` : ""}
    <p class="meta-block">完成后不会自动解锁全书报告，请手动点击“完成阅读”。</p>
    ${jsonDetails({ reading_status: status, last_submit: lastSubmit })}
  `, status?.required_coverage_complete ? "success-card" : "warning-card");
}

async function startAutoSurveyReading(button) {
  if (autoSurveyState.status === "running") return;
  requireBook();
  button.disabled = true;
  const original = button.textContent;
  button.textContent = "阅读中...";
  autoSurveyState.status = "running";
  autoSurveyState.requestedPause = false;
  autoSurveyState.lastError = null;
  autoSurveyState.completed = 0;
  autoSurveyState.usage = { inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheCreationTokens: 0 };
  renderClaudeCacheStatus();
  renderAutoSurveyState();
    startTask("reading", "一键快速覆盖阅读");
  try {
    updateTaskProgress(8, "检查 Claude 网页桥接");
    await ensureClaudeBridgeReady();
    updateTaskProgress(15, "准备快速覆盖阅读会话");
    await ensureSurveySession();

    let status = await refreshReadingStatus();
    while (!status?.required_coverage_complete) {
      if (autoSurveyState.requestedPause) {
        autoSurveyState.status = "paused";
        addTaskStep("warn", "已在章节边界暂停，可点击继续。");
        setResult(renderAutoSurveyResult(status));
        saveLocalState();
        renderAutoSurveyState();
        finishTask({ ok: true, paused: true, reading_status: status });
        return;
      }

      const percent = Math.max(15, Math.min(90, Number(status?.l1_coverage_percent || 0)));
      updateTaskProgress(percent, "读取下一章并生成 L1 笔记");
      const next = await api(`/api/reading/session/${encodeURIComponent(state.currentSessionId)}/next`, {
        method: "POST",
        body: JSON.stringify({ batch_chapters: 1 }),
      });
      const chapter = (next.chapters || [])[0];
      if (!chapter) {
        status = await refreshReadingStatus();
        break;
      }

      autoSurveyState.currentChapter = chapter.chapter_index;
      renderAutoSurveyState();
      addTaskStep("info", `读取第 ${chapter.chapter_index} 章，发送给 Claude 生成 L1 笔记。`);
      const claude = await api("/api/claude/chat", {
        method: "POST",
        body: JSON.stringify({
          book: state.currentBook.book_id,
          message: buildReadingNotePrompt(chapter),
          mode: "continue",
          context: { reading_session_id: state.currentSessionId, chapter_index: chapter.chapter_index, required_level: selectedAutoReadingLevel(chapter.required_level) },
        }),
      });
      const usage = addClaudeUsage(claude);
      await warmClaudeCacheIfNeeded(chapter, usage);
      const note = extractClaudeNoteText(claude);
      if (!note) throw new Error("Claude 没有返回可提交的笔记文本。");

      updateTaskProgress(percent + 3, `提交第 ${chapter.chapter_index} 章笔记`);
      const submit = await api(`/api/reading/session/${encodeURIComponent(state.currentSessionId)}/submit-note`, {
        method: "POST",
        body: JSON.stringify({ chapter: chapter.chapter_index, text: note }),
      });
      autoSurveyState.completed += 1;
      addTaskStep("ok", `第 ${chapter.chapter_index} 章笔记已通过校验。`);
      status = await refreshReadingStatus();
      setResult(renderAutoSurveyResult(status, submit));
      saveLocalState();
    }

    autoSurveyState.status = "stopped";
    updateTaskProgress(100, "快速覆盖 L1 已完成");
    addTaskStep("ok", "快速覆盖已完成，请手动点击“完成阅读”。");
    renderAutoSurveyState();
    saveLocalState();
    finishTask({ ok: true, reading_status: status });
    setResult(renderAutoSurveyResult(status));
  } catch (error) {
    autoSurveyState.status = "error";
    autoSurveyState.lastError = normalizeError(error);
    renderAutoSurveyState();
    saveLocalState();
    failTask(error);
    showError(error);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function startAutoReading(button) {
  return startAutoSurveyReading(button);
}

function pauseAutoSurveyReading() {
  if (autoSurveyState.status !== "running") return;
  autoSurveyState.requestedPause = true;
  autoSurveyState.status = "pausing";
  addTaskStep("warn", "收到暂停请求，将在当前章节提交后暂停。");
  renderAutoSurveyState();
  saveLocalState();
}

async function resumeAutoSurveyReading() {
  if (autoSurveyState.status !== "paused" && autoSurveyState.status !== "stopped" && autoSurveyState.status !== "error") return;
  await startAutoSurveyReading($("resumeAutoSurveyBtn"));
}

async function ingestBook(button) {
  await withButtonLoading(button, "导入中...", async () => {
    startTask("ingest", "导入小说");
    updateTaskProgress(20, "检查路径");
    const data = await api("/api/ingest", {
      method: "POST",
      body: JSON.stringify({ path: $("ingestPath").value.trim(), book_id: $("ingestId").value.trim(), force: $("ingestForce").checked }),
    });
    updateTaskProgress(80, "刷新书库");
    await loadBooks(data.book_id);
    finishTask(data);
    setResult(card("导入完成", `<p>${escapeHtml(data.book_id)}</p>${jsonDetails(data)}`, "success-card"));
  });
}

async function readText(button) {
  await withButtonLoading(button, "读取中...", async () => {
    startTask("reading", "读取章节");
    const book = requireBook();
    const chunk = $("readChunk").value.trim();
    const chapter = $("readChapter").value || "1";
    const url = chunk
      ? `/api/read?book=${encodeURIComponent(book)}&chunk=${encodeURIComponent(chunk)}`
      : `/api/read?book=${encodeURIComponent(book)}&chapter=${encodeURIComponent(chapter)}`;
    updateTaskProgress(60, "等待后端返回文本");
    const data = await api(url);
    finishTask(data);
    setResult(card("阅读结果", renderEvidenceList(data.chunks || []) + jsonDetails(data), "success-card"));
  });
}

async function searchOrAsk(kind, button) {
  await withButtonLoading(button, kind === "ask" ? "生成中..." : "搜索中...", async () => {
    const query = $("searchQuery").value.trim();
    if (!query) throw new Error("请输入搜索或问答内容。");
    startTask(kind, kind === "ask" ? "问答证据包" : "搜索证据");
    updateTaskProgress(20, "准备请求");
    const path = kind === "ask" ? "/api/ask" : "/api/search";
    const body = kind === "ask"
      ? { book: requireBook(), question: query, semantic: $("searchSemantic").checked }
      : { book: requireBook(), query, top: Number($("searchTop").value || 8), semantic: $("searchSemantic").checked };
    updateTaskProgress(60, "检索证据");
    const data = await api(path, { method: "POST", body: JSON.stringify(body) });
    saveLocalState();
    finishTask(data);
    setResult(kind === "ask"
      ? card("问答证据包", renderEvidenceList(data.evidence || []) + jsonDetails(data), "success-card")
      : card("搜索结果", renderEvidenceList(data.results || []) + jsonDetails(data), "success-card"));
  });
}

async function runReport(action, button) {
  await withButtonLoading(button, "生成中...", async () => {
    startTask(action, `生成${translateReportAction(action)}`);
    updateTaskProgress(10, "准备请求");
    updateTaskProgress(30, "读取当前书状态");
    const data = await api(`/api/action/${action}`, { method: "POST", body: JSON.stringify({ book: requireBook() }) });
    updateTaskProgress(90, "刷新文档");
    await refreshAndOpenGeneratedDocument(data);
    finishTask(data);
    setResult(renderReportResult(data));
    bindDocumentLinks();
  });
}

async function runStyle(button) {
  await withButtonLoading(button, "生成中...", async () => {
    startTask("style", "风格分析");
    updateTaskProgress(10, "准备请求");
    const data = await api("/api/style", {
      method: "POST",
      body: JSON.stringify({ book: requireBook(), scene: $("styleScene").value, write: $("styleWrite").checked, json: true }),
    });
    updateTaskProgress(90, "渲染风格证据");
    await refreshAndOpenGeneratedDocument(data);
    finishTask(data);
    setResult(renderStylePacket(data));
  });
}

function readPredictForm(save = true) {
  const data = {
    question: $("predictQuestion")?.value || "",
    scope: $("predictScope")?.value || "general",
    horizon: $("predictHorizon")?.value || "next-arc",
    anchor_chapter: $("predictAnchorChapter")?.value || "",
    anchor_chunk: $("predictAnchorChunk")?.value || "",
    semantic: $("predictSemantic")?.checked || false,
    write: $("predictWrite")?.checked || false,
    scope_mode: $("predictScopeMode")?.value || "partial",
    session_id: $("predictSessionId")?.value || "",
    allow_unfinalized: $("predictAllowUnfinalized")?.checked || false,
  };
  if (save) saveLocalState();
  return data;
}

function writePredictForm(data) {
  if (!data) return;
  if ($("predictQuestion")) $("predictQuestion").value = data.question || "";
  if ($("predictScope")) $("predictScope").value = data.scope || "general";
  if ($("predictHorizon")) $("predictHorizon").value = data.horizon || "next-arc";
  if ($("predictAnchorChapter")) $("predictAnchorChapter").value = data.anchor_chapter || "";
  if ($("predictAnchorChunk")) $("predictAnchorChunk").value = data.anchor_chunk || "";
  if ($("predictSemantic")) $("predictSemantic").checked = !!data.semantic;
  if ($("predictWrite")) $("predictWrite").checked = !!data.write;
  if ($("predictScopeMode")) $("predictScopeMode").value = data.scope_mode || "partial";
  if ($("predictSessionId")) $("predictSessionId").value = data.session_id || "";
  if ($("predictAllowUnfinalized")) $("predictAllowUnfinalized").checked = !!data.allow_unfinalized;
}

async function runPredict(button) {
  await withButtonLoading(button, "预测中...", async () => {
    startTask("predict", "生成后续剧情预测");
    addTaskStep("info", "predict 不生成正文，只生成概率化分析包");
    updateTaskProgress(10, "准备请求");
    const form = readPredictForm(false);
    const payload = {
      book: requireBook(),
      question: form.question,
      scope: form.scope,
      horizon: form.horizon,
      anchor_chapter: form.anchor_chapter ? Number(form.anchor_chapter) : null,
      anchor_chunk: form.anchor_chunk || null,
      semantic: form.semantic,
      write: form.write,
      scope_mode: form.scope_mode,
      session_id: form.session_id || state.currentSessionId || null,
      allow_unfinalized: form.allow_unfinalized,
    };
    updateTaskProgress(30, "读取当前书状态");
    addTaskStep("info", "等待后端生成预测包");
    updateTaskProgress(60, "处理中");
    const data = await api("/api/predict", { method: "POST", body: JSON.stringify(payload) });
    updateTaskProgress(90, "渲染预测结果");
    await refreshAndOpenGeneratedDocument(data);
    finishTask(data);
    saveLocalState();
    setResult(renderPredictionPacket(data));
  });
}

function readContinueForm(save = true) {
  const data = {
    after_chapter: $("contChapter")?.value || "",
    after_chunk: $("contChunk")?.value || "",
    outline: $("contOutline")?.value || "",
    scene: $("contScene")?.value || "",
    semantic: $("contSemantic")?.checked || false,
    write: $("contWrite")?.checked || false,
    length: $("contLength")?.value || "medium",
  };
  if (save) saveLocalState();
  return data;
}

function writeContinueForm(data) {
  if (!data) return;
  if ($("contChapter")) $("contChapter").value = data.after_chapter || "";
  if ($("contChunk")) $("contChunk").value = data.after_chunk || "";
  if ($("contOutline")) $("contOutline").value = data.outline || "";
  if ($("contScene")) $("contScene").value = data.scene || "";
  if ($("contSemantic")) $("contSemantic").checked = !!data.semantic;
  if ($("contWrite")) $("contWrite").checked = !!data.write;
  if ($("contLength")) $("contLength").value = data.length || "medium";
}

async function runContinue(button) {
  await withButtonLoading(button, "生成中...", async () => {
    startTask("continue", "生成续写任务包");
    updateTaskProgress(10, "准备请求");
    const form = readContinueForm(false);
    if (form.after_chapter && form.after_chunk) throw new Error("接在章节后 和 接在文本块后 只能填一个。");
    const data = await api("/api/continue", {
      method: "POST",
      body: JSON.stringify({ book: requireBook(), ...form }),
    });
    updateTaskProgress(90, "刷新文档");
    await refreshAndOpenGeneratedDocument(data);
    finishTask(data);
    saveLocalState();
    setResult(renderContinuationPacket(data));
  });
}

async function runEmbed(button) {
  await withButtonLoading(button, "构建中...", async () => {
    startTask("embedding", "构建语义索引");
    updateTaskProgress(0, "可能耗时较久，等待后端返回", "indeterminate");
    addTaskStep("warn", "当前没有真实进度接口，不伪造文本块进度");
    const data = await api("/api/embed", {
      method: "POST",
      body: JSON.stringify({ book: requireBook(), batch_size: Number($("embedBatch").value || 4), max_chars: Number($("embedMaxChars").value || 1500) }),
    });
    finishTask(data);
    setResult(card("语义索引构建完成", `
      <div class="summary-grid">
        <div><span>文本块</span><strong>${escapeHtml(data.chunks ?? "--")}</strong></div>
        <div><span>模型</span><strong>${escapeHtml(data.model || "--")}</strong></div>
        <div><span>向量后端</span><strong>sqlite_cosine</strong></div>
      </div>${jsonDetails(data)}
    `, "success-card"));
    await loadHealth().catch(() => {});
  });
}

async function sendClaude(button) {
  await withButtonLoading(button, "发送中...", async () => {
    const message = $("claudeMessage").value.trim();
    if (!message) throw new Error("请输入要发送给 Claude 的内容。");
    startTask("claude", "Claude 调用");
    updateTaskProgress(20, "准备上下文");
    const data = await api("/api/claude/chat", {
      method: "POST",
      body: JSON.stringify({ book: state.currentBook?.book_id || null, message, mode: $("claudeMode").value, context: { current_document: state.currentDocument, last_result: state.lastResult } }),
    });
    finishTask(data);
    setResult(card("Claude 回复", `<pre class="text-output">${escapeHtml(data.reply || data.stdout || "")}</pre>${jsonDetails(data)}`, "success-card"));
  });
}

async function loadDocuments() {
  const book = requireBook();
  const data = await api(`/api/documents?book=${encodeURIComponent(book)}`);
  state.documents = data.documents || [];
  renderDocumentList();
  return data;
}

function renderDocumentList() {
  const list = $("documentList");
  if (!state.documents.length) {
    list.innerHTML = `<div class="empty-state">暂无生成文档。</div>`;
    return;
  }
  const groups = {};
  for (const doc of state.documents) {
    groups[doc.category] ||= [];
    groups[doc.category].push(doc);
  }
  list.innerHTML = Object.entries(groups).map(([category, docs]) => `
    <section class="doc-group">
      <h3>${escapeHtml(translateDocumentCategory(category))}</h3>
      ${docs.map((doc) => `<button class="document-item" data-path="${escapeHtml(doc.path)}">
        <strong>${escapeHtml(doc.name)}</strong>
        <span>${formatSize(doc.size)}</span>
      </button>`).join("")}
    </section>
  `).join("");
  list.querySelectorAll(".document-item").forEach((button) => {
    button.addEventListener("click", () => openDocument(button.dataset.path).catch(showError));
  });
}

async function openDocument(path) {
  const book = requireBook();
  const data = await api(`/api/document?book=${encodeURIComponent(book)}&path=${encodeURIComponent(path)}`);
  state.currentDocument = data;
  state.documentView = data.document.extension === ".json" ? "source" : "preview";
  renderDocument();
}

function renderDocument() {
  const output = $("documentOutput");
  if (!state.currentDocument) {
    $("documentTitle").textContent = "未选择文档";
    $("documentMeta").textContent = "生成文档后会自动打开";
    $("docDownload").hidden = true;
    output.innerHTML = "";
    return;
  }
  const doc = state.currentDocument.document;
  $("documentTitle").textContent = doc.name;
  $("documentMeta").textContent = doc.path;
  $("docDownload").hidden = false;
  $("docDownload").href = `/api/document/download?book=${encodeURIComponent(doc.book_id)}&path=${encodeURIComponent(doc.path)}`;
  const content = state.currentDocument.content || "";
  if (state.documentView === "source" || doc.extension !== ".md") {
    output.innerHTML = `<pre class="code-box document-code">${escapeHtml(prettyContent(content, doc.extension))}</pre>`;
  } else {
    output.innerHTML = `<div class="markdown-body">${renderMarkdown(content)}</div>`;
  }
}

async function refreshAndOpenGeneratedDocument(data) {
  if (!data?.documents?.length && !data?.output_paths?.length) {
    await loadDocuments().catch(() => {});
    return;
  }
  await loadDocuments().catch(() => {});
  const docs = data.documents || [];
  const target = docs.find((doc) => doc.extension === ".md") || docs[0];
  if (target) {
    $("recentDocumentHint").textContent = `刚刚生成：${target.path}`;
    await openDocument(target.path);
  }
}

function prettyContent(content, ext) {
  if (ext === ".json") {
    try { return JSON.stringify(JSON.parse(content), null, 2); } catch { return content; }
  }
  return content;
}

function renderMarkdown(markdown) {
  const lines = escapeHtml(markdown).split("\n");
  const html = [];
  let inList = false;
  for (const line of lines) {
    if (line.startsWith("# ")) {
      if (inList) { html.push("</ul>"); inList = false; }
      html.push(`<h1>${line.slice(2)}</h1>`);
    } else if (line.startsWith("## ")) {
      if (inList) { html.push("</ul>"); inList = false; }
      html.push(`<h2>${line.slice(3)}</h2>`);
    } else if (line.startsWith("### ")) {
      if (inList) { html.push("</ul>"); inList = false; }
      html.push(`<h3>${line.slice(4)}</h3>`);
    } else if (line.startsWith("- ")) {
      if (!inList) { html.push("<ul>"); inList = true; }
      html.push(`<li>${line.slice(2)}</li>`);
    } else if (!line.trim()) {
      if (inList) { html.push("</ul>"); inList = false; }
      html.push("<br />");
    } else {
      if (inList) { html.push("</ul>"); inList = false; }
      html.push(`<p>${line}</p>`);
    }
  }
  if (inList) html.push("</ul>");
  return html.join("");
}

function bindDocumentLinks() {
  document.querySelectorAll("[data-doc-path]").forEach((button) => {
    button.addEventListener("click", () => openDocument(button.dataset.docPath).catch(showError));
  });
}

function formatSize(value) {
  if (!Number.isFinite(Number(value))) return "--";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function bind() {
  $("refreshBooks").addEventListener("click", () => loadBooks().catch(showError));
  $("healthBtn").addEventListener("click", () => withButtonLoading($("healthBtn"), "检查中...", loadHealth).catch(showError));
  $("ingestBtn").addEventListener("click", () => ingestBook($("ingestBtn")).catch((error) => { failTask(error); showError(error); }));
  $("createSurveyBtn").addEventListener("click", () => createReadingSession("survey", $("createSurveyBtn")).catch((error) => { failTask(error); showError(error); }));
  $("createBalancedBtn").addEventListener("click", () => createReadingSession("balanced", $("createBalancedBtn")).catch((error) => { failTask(error); showError(error); }));
  $("createDeepBtn").addEventListener("click", () => createReadingSession("deep", $("createDeepBtn")).catch((error) => { failTask(error); showError(error); }));
  $("readNextBtn").addEventListener("click", () => runReadNext($("readNextBtn")).catch((error) => { failTask(error); showError(error); }));
  $("submitNoteBtn").addEventListener("click", () => submitNote($("submitNoteBtn")).catch((error) => { failTask(error); showError(error); }));
  $("finalizeBtn").addEventListener("click", () => finalizeReading($("finalizeBtn")).catch((error) => { failTask(error); showError(error); }));
  $("autoSurveyBtn").addEventListener("click", () => startAutoReading($("autoSurveyBtn")).catch((error) => { failTask(error); showError(error); }));
  $("pauseAutoSurveyBtn").addEventListener("click", pauseAutoSurveyReading);
  $("resumeAutoSurveyBtn").addEventListener("click", () => resumeAutoSurveyReading().catch((error) => { failTask(error); showError(error); }));
  $("readBtn").addEventListener("click", () => readText($("readBtn")).catch((error) => { failTask(error); showError(error); }));
  $("searchBtn").addEventListener("click", () => searchOrAsk("search", $("searchBtn")).catch((error) => { failTask(error); showError(error); }));
  $("askBtn").addEventListener("click", () => searchOrAsk("ask", $("askBtn")).catch((error) => { failTask(error); showError(error); }));
  document.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => runReport(button.dataset.action, button).catch((error) => { failTask(error); showError(error); }));
  });
  $("styleBtn").addEventListener("click", () => runStyle($("styleBtn")).catch((error) => { failTask(error); showError(error); }));
  $("predictBtn").addEventListener("click", () => runPredict($("predictBtn")).catch((error) => { failTask(error); showError(error); }));
  $("continueBtn").addEventListener("click", () => runContinue($("continueBtn")).catch((error) => { failTask(error); showError(error); }));
  $("embedBtn").addEventListener("click", () => runEmbed($("embedBtn")).catch((error) => { failTask(error); showError(error); }));
  $("sendClaudeBtn").addEventListener("click", () => sendClaude($("sendClaudeBtn")).catch((error) => { failTask(error); showError(error); }));
  $("refreshDocs").addEventListener("click", () => loadDocuments().catch(showError));
  $("docPreviewBtn").addEventListener("click", () => { state.documentView = "preview"; renderDocument(); });
  $("docSourceBtn").addEventListener("click", () => { state.documentView = "source"; renderDocument(); });
}

async function init() {
  const saved = restoreLocalState();
  bind();
  renderActiveTask();
  renderReadingProgress(null);
  renderAutoSurveyState();
  await loadHealth().catch(showError);
  await loadBooks(saved.selectedBookId).catch(showError);
}

setInterval(() => {
  if (taskState.active?.status === "running") renderActiveTask();
}, 1000);

init();
