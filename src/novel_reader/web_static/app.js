const storageKey = "novel-reader-web-state-v4";

const state = {
  books: [],
  currentBook: null,
  csrfToken: "",
  documents: [],
  currentDocument: null,
  documentView: "preview",
  currentSessionId: "",
  readingStatus: null,
  activeArtifact: null,
  chatMessages: [],
  chatContext: null,
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

function showToast(message) {
  const toast = $("toast");
  toast.textContent = message;
  toast.hidden = false;
  setTimeout(() => { toast.hidden = true; }, 3200);
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
    if (!state.csrfToken) await ensureCsrfToken();
    headers["Content-Type"] = "application/json";
    headers["X-Novel-Reader-Token"] = state.csrfToken;
  }
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({ ok: false, error: "响应不是 JSON。" }));
  if (!response.ok || data.ok === false) {
    const error = new Error(typeof data.error === "string" ? data.error : (data.error?.message || `请求失败：${response.status}`));
    error.payload = data;
    throw error;
  }
  return data;
}

async function ensureCsrfToken() {
  const response = await fetch("/api/health", { cache: "no-store" });
  const data = await response.json().catch(() => ({}));
  if (data.csrf_token) state.csrfToken = data.csrf_token;
  return state.csrfToken;
}

function requireBook() {
  if (!state.currentBook) throw new Error("请先选择一本书。");
  return state.currentBook.book_id;
}

function saveLocalState() {
  const sessions = JSON.parse(localStorage.getItem(`${storageKey}:sessions`) || "{}");
  if (state.currentBook?.book_id && state.currentSessionId) {
    sessions[state.currentBook.book_id] = state.currentSessionId;
  }
  localStorage.setItem(`${storageKey}:sessions`, JSON.stringify(sessions));
  localStorage.setItem(storageKey, JSON.stringify({
    selectedBookId: state.currentBook?.book_id || "",
    currentSessionId: state.currentSessionId || "",
    lastPredictForm: readPredictForm(false),
    lastContinueForm: readContinueForm(false),
    lastSearchQuery: $("searchQuery")?.value || "",
    documentFilter: $("documentCategoryFilter")?.value || "",
  }));
}

function restoreLocalState() {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || "{}");
    if (saved.lastPredictForm) writePredictForm(saved.lastPredictForm);
    if (saved.lastContinueForm) writeContinueForm(saved.lastContinueForm);
    if (saved.lastSearchQuery && $("searchQuery")) $("searchQuery").value = saved.lastSearchQuery;
    if (saved.documentFilter && $("documentCategoryFilter")) $("documentCategoryFilter").value = saved.documentFilter;
    return saved;
  } catch {
    return {};
  }
}

function sessionForBook(bookId) {
  const sessions = JSON.parse(localStorage.getItem(`${storageKey}:sessions`) || "{}");
  return sessions[bookId] || "";
}

function startTask(type, name) {
  taskState.active = {
    id: `${type}-${Date.now()}`,
    type,
    name,
    status: "running",
    startedAt: Date.now(),
    endedAt: null,
    progress: { mode: "determinate", percent: 8, label: "准备中..." },
    steps: [{ time: nowTime(), level: "info", text: "任务已创建" }],
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
  taskState.active.result = result;
  taskState.active.progress = { mode: "determinate", percent: 100, label: "完成" };
  taskState.active.steps.push({ time: nowTime(), level: "ok", text: "任务完成" });
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
  taskState.active.steps.push({ time: nowTime(), level: "error", text: taskState.active.error.message || "任务失败" });
  taskState.history.unshift(taskState.active);
  taskState.history = taskState.history.slice(0, 8);
  renderActiveTask();
  renderTaskHistory();
}

function elapsedText(task) {
  if (!task) return "0s";
  return `${Math.max(0, Math.round(((task.endedAt || Date.now()) - task.startedAt) / 1000))}s`;
}

function translateTaskStatus(status) {
  return { running: "运行中", success: "成功", error: "失败", idle: "空闲" }[status] || status;
}

function translateTaskType(type) {
  return {
    reading: "阅读",
    embedding: "语义索引",
    ingest: "导入",
    search: "搜索",
    ask: "问答",
    style: "风格",
    predict: "预测",
    continue: "续写",
    claude: "Claude",
    outline: "大纲",
    map: "地图",
    analyze: "分析",
  }[type] || type;
}

function translateAutoSurveyStatus(status) {
  return {
    stopped: "已停止",
    running: "运行中",
    paused: "已暂停",
    pausing: "暂停中",
    error: "出错",
  }[status] || status;
}

function renderActiveTask() {
  const task = taskState.active;
  const badge = $("taskStatusBadge");
  if (!task) {
    $("taskSubtitle").textContent = "当前没有任务运行";
    badge.textContent = "空闲";
    badge.className = "badge neutral";
    $("taskProgressLabel").textContent = "等待操作";
    $("taskElapsed").textContent = "0s";
    $("taskProgressFill").style.width = "0%";
    $("taskProgressFill").classList.remove("indeterminate");
    $("taskLog").innerHTML = "";
    renderTaskHistory();
    return;
  }
  $("taskSubtitle").textContent = `${task.name} · ${translateTaskType(task.type)}`;
  badge.textContent = translateTaskStatus(task.status);
  badge.className = `badge ${task.status === "success" ? "ok" : task.status === "error" ? "bad" : "info"}`;
  $("taskProgressLabel").textContent = task.progress.label;
  $("taskElapsed").textContent = elapsedText(task);
  $("taskProgressFill").style.width = task.progress.mode === "indeterminate" ? "45%" : `${task.progress.percent}%`;
  $("taskProgressFill").classList.toggle("indeterminate", task.progress.mode === "indeterminate");
  $("taskLog").innerHTML = task.steps.map((step) => `<div class="task-step ${step.level}"><span>${escapeHtml(step.time)}</span><p>${escapeHtml(step.text)}</p></div>`).join("");
}

function renderTaskHistory() {
  const target = $("taskHistory");
  const items = taskState.history.slice(0, 3);
  target.innerHTML = items.length
    ? `<h3>最近任务</h3>${items.map((task) => `<div class="task-history-item ${task.status}"><span>${escapeHtml(translateTaskStatus(task.status))}</span><strong>${escapeHtml(task.name)}</strong><em>${escapeHtml(elapsedText(task))}</em></div>`).join("")}`
    : "";
}

async function withButtonLoading(button, loadingText, fn) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = loadingText;
  try {
    return await fn();
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function jsonDetails(data) {
  return `<details class="json-details"><summary>查看原始 JSON</summary><pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre></details>`;
}

function card(title, body, kind = "") {
  return `<article class="result-card ${kind}"><h3>${escapeHtml(title)}</h3>${body}</article>`;
}

function setResult(html) {
  $("resultPreview").innerHTML = html;
}

function renderEvidenceList(items = []) {
  if (!items.length) return `<div class="empty-state">没有证据片段。</div>`;
  return `<div class="evidence-list">${items.map((item) => `<article class="evidence-card"><div class="evidence-head"><strong>${escapeHtml(item.chunk_id || item.source || "证据")}</strong><span>第 ${escapeHtml(item.chapter ?? item.chapter_index ?? "--")} 章</span></div><p>${escapeHtml(item.reason || item.source || "")}</p><blockquote>${escapeHtml(item.excerpt || item.snippet || item.text || "")}</blockquote></article>`).join("")}</div>`;
}

function artifactTitle(type) {
  return {
    predict: "剧情预测包",
    continue: "续写任务包",
    style: "风格证据包",
    outline: "剧情大纲",
    map: "剧情地图",
    analyze: "写作分析",
    ask: "问答证据包",
    search: "搜索证据",
    document: "当前文档",
  }[type] || "当前产物";
}

function summarizeArtifact(type, data) {
  if (!data) return "";
  if (type === "predict") return (data.predictions || []).map((p) => p.claim).slice(0, 3).join("\n") || "预测包已生成。";
  if (type === "continue") return data.continuation_goal?.outline || "续写任务包已生成。";
  if (type === "document") return data.document?.path || "当前文档";
  if (data.documents?.length) return data.documents.map((doc) => doc.path).join("\n");
  if (data.evidence?.length) return data.evidence.map((item) => item.chunk_id || item.reason).slice(0, 5).join("\n");
  return "产物已生成。";
}

function setActiveArtifact(type, data, options = {}) {
  state.activeArtifact = {
    type,
    title: options.title || artifactTitle(type),
    data,
    documents: data?.documents || [],
    summary: options.summary || summarizeArtifact(type, data),
    createdAt: Date.now(),
  };
  renderArtifactActions();
  setChatContext({ kind: "artifact", title: state.activeArtifact.title, data: state.activeArtifact.data });
}

function renderArtifactActions() {
  const artifact = state.activeArtifact;
  $("activeArtifactBadge").textContent = artifact ? artifactTitle(artifact.type) : "无产物";
  $("activeArtifactBadge").className = `badge ${artifact ? "info" : "neutral"}`;
  $("activeArtifactMeta").textContent = artifact ? artifact.summary || artifact.title : "生成预测包、续写包或报告后会出现在这里。";
  for (const id of ("sendArtifactBtn openArtifactDocBtn copyArtifactSummaryBtn toggleArtifactJsonBtn").split(" ")) {
    $(id).disabled = !artifact;
  }
}

async function sendArtifactToClaude() {
  if (!state.activeArtifact) throw new Error("当前没有可发送的产物。");
  const message = `请基于当前 Novel Reader 产物继续处理：${state.activeArtifact.title}`;
  await sendClaudeMessage(message, {
    active_artifact: state.activeArtifact,
    current_document: state.currentDocument,
  });
}

function setChatContext(context) {
  state.chatContext = context;
  $("chatAttachment").textContent = context ? `已附加：${context.title || context.kind}` : "未附加上下文";
}

function renderChatMessages() {
  const target = $("chatMessages");
  if (!state.chatMessages.length) {
    target.innerHTML = `<div class="empty-state">还没有对话。生成任务包后可一键发送给 Claude。</div>`;
    return;
  }
  target.innerHTML = state.chatMessages.map((item) => `<article class="chat-message ${item.role}"><strong>${item.role === "user" ? "你" : "Claude"}</strong><p>${escapeHtml(item.text)}</p></article>`).join("");
  target.scrollTop = target.scrollHeight;
}

async function sendClaudeMessage(message, context = null) {
  const trimmed = String(message || "").trim();
  if (!trimmed) throw new Error("请输入要发送给 Claude 的内容。");
  state.chatMessages.push({ role: "user", text: trimmed, time: Date.now() });
  renderChatMessages();
  startTask("claude", "发送给 Claude");
  updateTaskProgress(15, "准备上下文");
  const data = await api("/api/claude/chat", {
    method: "POST",
    body: JSON.stringify({
      book: state.currentBook?.book_id || null,
      message: trimmed,
      mode: $("claudeMode").value,
      context: context || {
        active_artifact: state.activeArtifact,
        current_document: state.currentDocument,
      },
    }),
  });
  const reply = data.reply || data.stdout || "";
  state.chatMessages.push({ role: "assistant", text: reply || "Claude 已返回空响应。", time: Date.now() });
  renderChatMessages();
  finishTask(data);
  return data;
}

function renderStatusSummary(data) {
  return card("书籍状态", `<div class="summary-grid"><div><span>章节</span><strong>${escapeHtml(data.chapter_count ?? "--")}</strong></div><div><span>文本块</span><strong>${escapeHtml(data.chunk_count ?? "--")}</strong></div><div><span>摘要覆盖</span><strong>${escapeHtml(data.summary_coverage?.percent ?? data.summary_coverage_percent ?? 0)}%</strong></div><div><span>向量后端</span><strong>${escapeHtml(data.vector_backend || "sqlite_cosine")}</strong></div></div>${jsonDetails(data)}`, "success-card");
}

function renderPredictionPacket(packet) {
  const predictions = packet.predictions || [];
  return `${card("当前剧情状态", `<div class="summary-grid"><div><span>最新章节</span><strong>${escapeHtml(packet.current_state?.latest_chapter ?? "--")}</strong></div><div><span>摘要覆盖</span><strong>${escapeHtml(packet.book?.summary_coverage_percent ?? 0)}%</strong></div><div><span>范围</span><strong>${escapeHtml(packet.prediction_goal?.scope)}</strong></div><div><span>跨度</span><strong>${escapeHtml(packet.prediction_goal?.horizon)}</strong></div></div>`, "success-card")}${card("剧情预测", predictions.map((item) => `<article class="prediction-card"><div class="prediction-head"><span class="badge ${item.probability === "high" ? "ok" : item.probability === "medium" ? "warn" : "neutral"}">${escapeHtml(item.probability || "unknown")}</span><strong>${escapeHtml(item.type || "预测")}</strong></div><p class="prediction-claim">${escapeHtml(item.claim)}</p><div class="confidence-row"><span>置信度</span><div class="progress-track"><div class="progress-fill" style="width:${Math.round((item.confidence || 0) * 100)}%"></div></div><strong>${escapeHtml(item.confidence)}</strong></div><p class="risk">${escapeHtml(item.risk || "")}</p></article>`).join("") || `<div class="empty-state">暂无预测。</div>`)}${card("证据", renderEvidenceList(packet.evidence || []))}${jsonDetails(packet)}`;
}

function renderContinuationPacket(packet) {
  return `${card("续写任务包", `<p>${escapeHtml(packet.continuation_goal?.outline || "已生成可交给 Claude 的续写任务包。")}</p>`, "success-card")}${card("最近上下文", renderEvidenceList(packet.recent_context || []))}${card("剧情证据", renderEvidenceList(packet.plot_evidence || []))}${jsonDetails(packet)}`;
}

function renderStylePacket(packet) {
  return `${card("风格画像", `<p>摘要覆盖：${escapeHtml(packet.summary_coverage_percent ?? 0)}%</p>`, "success-card")}${card("短引文证据", renderEvidenceList(packet.evidence || []))}${jsonDetails(packet)}`;
}

function renderReportResult(data) {
  const docs = data.documents || [];
  return card("文档已生成", `${docs.length ? `<ul>${docs.map((doc) => `<li><button class="link-button" data-doc-path="${escapeHtml(doc.path)}">${escapeHtml(doc.path)}</button></li>`).join("")}</ul>` : "<p>操作完成。</p>"}${jsonDetails(data)}`, "success-card");
}

function renderError(error) {
  const err = normalizeError(error);
  return `<article class="error-card"><h3>${escapeHtml(err.code || "操作失败")}</h3><p>${escapeHtml(err.message || "未知错误")}</p>${err.next_action ? `<p class="next-action">下一步：${escapeHtml(err.next_action)}</p>` : ""}<div class="button-row"><button id="recoverRefreshStatus">刷新状态</button><button id="recoverReadNext">read-next</button><button id="recoverFinalize">finalize</button><button id="recoverPartial">切换阶段性范围</button></div>${jsonDetails(err)}</article>`;
}

function bindRecoveryButtons() {
  $("recoverRefreshStatus")?.addEventListener("click", () => refreshReadingStatus().catch(showError));
  $("recoverReadNext")?.addEventListener("click", () => runReadNext($("readNextBtn")).catch(showError));
  $("recoverFinalize")?.addEventListener("click", () => finalizeReading($("finalizeBtn")).catch(showError));
  $("recoverPartial")?.addEventListener("click", () => {
    if ($("reportScope")) $("reportScope").value = "partial";
    if ($("predictScopeMode")) $("predictScopeMode").value = "partial";
    showToast("已切换为阶段性范围。");
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
  renderEmbeddingStatus(data.embedding || {});
  renderClaudeStatus(data.claude || {});
  return data;
}

function renderEmbeddingStatus(status) {
  const pill = $("embeddingPill");
  if (!status.configured) {
    pill.textContent = "Embedding 未配置";
    pill.className = "badge warn";
  } else if (status.available) {
    pill.textContent = "Embedding 可用";
    pill.className = "badge ok";
  } else {
    pill.textContent = "Embedding 不可用";
    pill.className = "badge bad";
  }
}

function renderClaudeStatus(status) {
  const pill = $("claudePill");
  if (!status.enabled) {
    pill.textContent = "Claude 未启用";
    pill.className = "badge warn";
    $("claudeBridgeHint").textContent = "用 start-web.ps1 -EnableClaudeChat 启动后可在网页中对话。";
  } else if (status.available) {
    pill.textContent = status.permission === "dangerous" ? "Claude dangerous" : "Claude 可用";
    pill.className = status.permission === "dangerous" ? "badge bad" : "badge ok";
    $("claudeBridgeHint").textContent = status.permission === "dangerous" ? "dangerous 模式已启用，请确认发送内容。" : "Claude bridge 已启用。";
  } else {
    pill.textContent = "Claude 未找到";
    pill.className = "badge bad";
    $("claudeBridgeHint").textContent = "未在 PATH 中找到 claude 命令。";
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
  const saved = restoreLocalState();
  const selected = preferredBookId || state.currentBook?.book_id || saved.selectedBookId;
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
    return `<button class="book-item${active}" data-book="${escapeHtml(book.book_id)}"><strong>${escapeHtml(book.title)}</strong><span>${escapeHtml(book.book_id)} · ${book.chapter_count} 章 · 摘要 ${coverage}%</span></button>`;
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
    $("bookMeta").textContent = "导入 TXT/Markdown 后开始。";
    $("selectedBookPill").textContent = "未选择书籍";
    return;
  }
  $("currentTitle").textContent = state.currentBook.title;
  $("bookMeta").textContent = `${state.currentBook.book_id} · ${state.currentBook.chapter_count} 章 · ${state.currentBook.chunk_count} chunks`;
  $("selectedBookPill").textContent = state.currentBook.title;
  $("selectedBookPill").className = "badge info";
  if ($("predictSessionId")) $("predictSessionId").value = state.currentSessionId || "";
}

async function loadStatus() {
  const data = await api(`/api/status/${encodeURIComponent(requireBook())}`);
  setResult(renderStatusSummary(data));
  return data;
}

async function createReadingSession(mode, button) {
  await withButtonLoading(button, "创建中...", async () => {
    startTask("reading", `创建 ${mode} session`);
    const data = await api("/api/reading/session", {
      method: "POST",
      body: JSON.stringify({ book: requireBook(), mode, goal: "full", deep_ratio: 0.25 }),
    });
    state.currentSessionId = data.session_id;
    if ($("predictSessionId")) $("predictSessionId").value = data.session_id;
    await refreshReadingStatus();
    saveLocalState();
    finishTask(data);
    setActiveArtifact("reading", data, { title: "阅读会话", summary: data.session_id });
    setResult(card("阅读会话已创建", jsonDetails(data), "success-card"));
  });
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
    $("sessionMeta").textContent = "未创建 reading session";
    setProgress("l1Progress", "l1ProgressText", 0);
    setProgress("l2Progress", "l2ProgressText", 0);
    setProgress("l3Progress", "l3ProgressText", 0);
    $("fullScopeState").textContent = "Full Scope: Locked";
    $("fullScopeState").className = "scope-state locked";
    return;
  }
  $("sessionMeta").textContent = `${status.session_id} · ${status.mode} · ${status.status}`;
  setProgress("l1Progress", "l1ProgressText", status.l1_coverage_percent || 0);
  setProgress("l2Progress", "l2ProgressText", status.l2_coverage_percent || 0);
  setProgress("l3Progress", "l3ProgressText", status.l3_coverage_percent || 0);
  $("fullScopeState").textContent = status.full_scope_allowed ? "Full Scope: Allowed" : status.required_coverage_complete ? "Full Scope: Ready to finalize" : "Full Scope: Locked";
  $("fullScopeState").className = `scope-state ${status.full_scope_allowed ? "allowed" : status.required_coverage_complete ? "ready" : "locked"}`;
  renderAutoSurveyState();
}

function setProgress(fillId, textId, percent) {
  const value = Math.max(0, Math.min(100, Number(percent) || 0));
  $(fillId).style.width = `${value}%`;
  $(textId).textContent = `${value}%`;
}

async function runReadNext(button) {
  if (!state.currentSessionId) throw new Error("请先创建 reading session。");
  await withButtonLoading(button, "读取中...", async () => {
    startTask("reading", "read-next");
    const data = await api(`/api/reading/session/${encodeURIComponent(state.currentSessionId)}/next`, {
      method: "POST",
      body: JSON.stringify({ batch_chapters: 1 }),
    });
    finishTask(data);
    setActiveArtifact("reading", data, { title: "下一章阅读包", summary: "可交给 Claude 生成章节笔记。" });
    setResult(card("下一章阅读包", jsonDetails(data), "success-card"));
  });
}

async function submitNote(button) {
  if (!state.currentSessionId) throw new Error("请先创建 reading session。");
  await withButtonLoading(button, "提交中...", async () => {
    startTask("reading", "提交章节笔记");
    const data = await api(`/api/reading/session/${encodeURIComponent(state.currentSessionId)}/submit-note`, {
      method: "POST",
      body: JSON.stringify({ chapter: Number($("noteChapter").value || 1), text: $("noteText").value }),
    });
    await refreshReadingStatus();
    finishTask(data);
    setResult(card("章节笔记已提交", jsonDetails(data), "success-card"));
  });
}

async function finalizeReading(button) {
  if (!state.currentSessionId) throw new Error("请先创建 reading session。");
  await withButtonLoading(button, "finalize...", async () => {
    startTask("reading", "finalize-reading");
    const data = await api(`/api/reading/session/${encodeURIComponent(state.currentSessionId)}/finalize`, { method: "POST", body: JSON.stringify({}) });
    await refreshReadingStatus();
    finishTask(data);
    setResult(card("Full-scope 已解锁", jsonDetails(data), "success-card"));
  });
}

function renderAutoSurveyState() {
  const target = $("autoSurveyState");
  const chapter = autoSurveyState.currentChapter ? ` · 第 ${autoSurveyState.currentChapter} 章` : "";
  target.textContent = `${translateAutoSurveyStatus(autoSurveyState.status)}${chapter}`;
  target.className = `auto-survey-state ${autoSurveyState.status}`;
}

async function ensureClaudeBridgeReady() {
  const status = await api("/api/claude/status");
  if (!status.enabled) throw new Error("Claude 网页桥接未启用，请用 -EnableClaudeChat 重启 Web。");
  if (!status.available) throw new Error("未找到 claude 命令。");
  return status;
}

async function ensureSurveySession() {
  if (state.currentSessionId && state.readingStatus?.mode === "survey") return state.currentSessionId;
  const data = await api("/api/reading/session", {
    method: "POST",
    body: JSON.stringify({ book: requireBook(), mode: "survey", goal: "full", deep_ratio: 0.25 }),
  });
  state.currentSessionId = data.session_id;
  await refreshReadingStatus();
  saveLocalState();
  return data.session_id;
}

function buildL1NotePrompt(chapterPack) {
  return buildStructuredNotePrompt(chapterPack, "L1_SKIMMED", ["one_sentence", "events", "characters", "evidence_chunks"], 100);
}

function buildL2NotePrompt(chapterPack) {
  return buildStructuredNotePrompt(chapterPack, "L2_READ", ["事件", "人物与动机", "冲突", "情节因果", "伏笔/回收", "设定/地点/势力", "时间线", "写作观察", "证据块"], 300);
}

function buildL3NotePrompt(chapterPack) {
  return buildStructuredNotePrompt(chapterPack, "L3_DEEP_READ", ["事件", "人物与动机", "冲突", "情节因果", "伏笔/回收", "设定/地点/势力", "时间线", "写作观察", "证据块", "scene_breakdown", "style_observation", "character_state", "continuity_constraints"], 600);
}

function buildStructuredNotePrompt(chapterPack, level, fields, minChars) {
  const chunks = chapterPack.chunks || [];
  const ids = chunks.map((chunk) => chunk.chunk_id);
  const perChunkBudget = Math.max(800, Math.floor(autoSurveyPromptBudget / Math.max(chunks.length, 1)) - 120);
  const chunkText = chunks.map((chunk) => `## ${chunk.chunk_id}\n${clipTextForPrompt(chunk.text || "", perChunkBudget)}`).join("\n\n");
  return [
    `请只根据下面原文生成 Novel Reader ${level} 结构化笔记。`,
    `笔记至少 ${minChars} 个非空白字符。`,
    `必须包含字段：${fields.join("、")}`,
    `evidence_chunks 必须引用本章 chunk_id：${ids.join(", ")}`,
    "不要输出 JSON，只输出可提交的 Markdown 笔记。",
    "",
    chunkText,
  ].join("\n");
}

function selectedAutoReadingLevel(requiredLevel) {
  const selected = $("autoReadingDepth")?.value || "auto";
  return selected === "auto" ? (requiredLevel || "L1_SKIMMED") : selected;
}

function buildReadingNotePrompt(chapterPack) {
  const level = selectedAutoReadingLevel(chapterPack.required_level);
  if (level === "L3_DEEP_READ") return buildL3NotePrompt(chapterPack);
  if (level === "L2_READ") return buildL2NotePrompt(chapterPack);
  return buildL1NotePrompt(chapterPack);
}

function clipTextForPrompt(text, maxChars) {
  const value = String(text || "");
  if (value.length <= maxChars) return value;
  return `${value.slice(0, Math.floor(maxChars * 0.6))}\n...[已截断]...\n${value.slice(-Math.floor(maxChars * 0.35))}`;
}

function extractClaudeNoteText(data) {
  const candidates = [data?.reply, data?.text, data?.stdout, data?.output, data?.message];
  return candidates.find((value) => typeof value === "string" && value.trim())?.trim() || "";
}

function extractClaudeUsage(data) {
  const usage = data?.usage || data?.parsed?.usage || {};
  const cache = data?.cache || {};
  return {
    inputTokens: Number(usage.input_tokens || usage.inputTokens || 0),
    outputTokens: Number(usage.output_tokens || usage.outputTokens || 0),
    cacheReadTokens: Number(cache.read_input_tokens || usage.cache_read_tokens || 0),
    cacheCreationTokens: Number(cache.creation_input_tokens || usage.cache_creation_tokens || 0),
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

function renderClaudeCacheStatus(usage = null) {
  const target = $("claudeCacheStatus");
  const label = usage?.available ? `本章命中率 ${(Number(usage.hitRate || 0) * 100).toFixed(1)}%` : "Claude 未返回缓存指标";
  target.innerHTML = `<div><strong>Claude 缓存</strong>：${escapeHtml(label)}</div><div class="usage-summary"><span>输入 ${autoSurveyState.usage.inputTokens}</span><span>输出 ${autoSurveyState.usage.outputTokens}</span><span>cache read ${autoSurveyState.usage.cacheReadTokens}</span></div>`;
}

async function warmClaudeCacheIfNeeded() {
  return null;
}

async function startAutoSurveyReading(button) {
  if (autoSurveyState.status === "running") return;
  requireBook();
  await withButtonLoading(button, "阅读中...", async () => {
    autoSurveyState.status = "running";
    autoSurveyState.requestedPause = false;
    autoSurveyState.completed = 0;
    renderAutoSurveyState();
    startTask("reading", "Claude 自动阅读（实验性）");
    try {
      await ensureClaudeBridgeReady();
      await ensureSurveySession();
      let status = await refreshReadingStatus();
      while (!status?.required_coverage_complete) {
        if (autoSurveyState.requestedPause) {
          autoSurveyState.status = "paused";
          renderAutoSurveyState();
          finishTask({ ok: true, paused: true, reading_status: status });
          return;
        }
        const next = await api(`/api/reading/session/${encodeURIComponent(state.currentSessionId)}/next`, { method: "POST", body: JSON.stringify({ batch_chapters: 1 }) });
        const chapter = (next.chapters || [])[0];
        if (!chapter) break;
        autoSurveyState.currentChapter = chapter.chapter_index || chapter.chapter;
        renderAutoSurveyState();
        const claude = await api("/api/claude/chat", {
          method: "POST",
          body: JSON.stringify({
            book: state.currentBook.book_id,
            message: buildReadingNotePrompt(chapter),
            mode: "continue",
            context: { reading_session_id: state.currentSessionId, chapter_index: autoSurveyState.currentChapter },
          }),
        });
        addClaudeUsage(claude);
        const note = extractClaudeNoteText(claude);
        if (!note) throw new Error("Claude 没有返回可提交的笔记。");
        await api(`/api/reading/session/${encodeURIComponent(state.currentSessionId)}/submit-note`, { method: "POST", body: JSON.stringify({ chapter: autoSurveyState.currentChapter, text: note }) });
        autoSurveyState.completed += 1;
        status = await refreshReadingStatus();
      }
      autoSurveyState.status = "stopped";
      renderAutoSurveyState();
      finishTask({ ok: true, reading_status: status });
    } catch (error) {
      autoSurveyState.status = "error";
      autoSurveyState.lastError = normalizeError(error);
      renderAutoSurveyState();
      failTask(error);
      showError(error);
    }
  });
}

async function startAutoReading(button) {
  return startAutoSurveyReading(button);
}

function pauseAutoSurveyReading() {
  if (autoSurveyState.status !== "running") return;
  autoSurveyState.requestedPause = true;
  autoSurveyState.status = "pausing";
  renderAutoSurveyState();
}

async function resumeAutoSurveyReading() {
  if (["paused", "stopped", "error"].includes(autoSurveyState.status)) {
    await startAutoSurveyReading($("resumeAutoSurveyBtn"));
  }
}

async function ingestBook(button) {
  await withButtonLoading(button, "导入中...", async () => {
    startTask("ingest", "导入小说");
    const data = await api("/api/ingest", { method: "POST", body: JSON.stringify({ path: $("ingestPath").value.trim(), book_id: $("ingestId").value.trim(), force: $("ingestForce").checked }) });
    await loadBooks(data.book_id);
    finishTask(data);
    setResult(card("导入完成", jsonDetails(data), "success-card"));
  });
}

async function readText(button) {
  await withButtonLoading(button, "读取中...", async () => {
    startTask("reading", "读取章节");
    const book = requireBook();
    const chunk = $("readChunk").value.trim();
    const chapter = $("readChapter").value || "1";
    const url = chunk ? `/api/read?book=${encodeURIComponent(book)}&chunk=${encodeURIComponent(chunk)}` : `/api/read?book=${encodeURIComponent(book)}&chapter=${encodeURIComponent(chapter)}`;
    const data = await api(url);
    finishTask(data);
    setActiveArtifact("read", data, { title: "阅读结果", summary: `第 ${chapter} 章阅读结果` });
    setResult(card("阅读结果", renderEvidenceList(data.chunks || []) + jsonDetails(data), "success-card"));
  });
}

async function searchOrAsk(kind, button) {
  await withButtonLoading(button, kind === "ask" ? "生成中..." : "搜索中...", async () => {
    const query = $("searchQuery").value.trim();
    if (!query) throw new Error("请输入搜索或问答内容。");
    startTask(kind, kind === "ask" ? "问答证据包" : "搜索证据");
    const path = kind === "ask" ? "/api/ask" : "/api/search";
    const payload = kind === "ask"
      ? { book: requireBook(), question: query, semantic: $("searchSemantic").checked }
      : { book: requireBook(), query, top: Number($("searchTop").value || 8), semantic: $("searchSemantic").checked };
    const data = await api(path, { method: "POST", body: JSON.stringify(payload) });
    finishTask(data);
    setActiveArtifact(kind, data, { title: kind === "ask" ? "问答证据包" : "搜索证据", summary: query });
    setResult(kind === "ask" ? card("问答证据包", renderEvidenceList(data.evidence || []) + jsonDetails(data), "success-card") : card("搜索结果", renderEvidenceList(data.results || []) + jsonDetails(data), "success-card"));
    saveLocalState();
  });
}

async function runReport(action, button) {
  await withButtonLoading(button, "生成中...", async () => {
    startTask(action, `生成${translateTaskType(action)}`);
    const data = await api(`/api/action/${action}`, {
      method: "POST",
      body: JSON.stringify({ book: requireBook(), scope: $("reportScope").value, session_id: state.currentSessionId || null, allow_unfinalized: false }),
    });
    await refreshAndOpenGeneratedDocument(data);
    finishTask(data);
    setActiveArtifact(action, data, { title: translateTaskType(action), summary: summarizeArtifact(action, data) });
    setResult(renderReportResult(data));
    bindDocumentLinks();
  });
}

async function runStyle(button) {
  await withButtonLoading(button, "生成中...", async () => {
    startTask("style", "风格分析");
    const data = await api("/api/style", { method: "POST", body: JSON.stringify({ book: requireBook(), scene: $("styleScene").value, write: $("styleWrite").checked, json: true }) });
    await refreshAndOpenGeneratedDocument(data);
    finishTask(data);
    setActiveArtifact("style", data, { title: "风格证据包", summary: "可交给 Claude 生成原创转写建议。" });
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
  if ($("predictWrite")) $("predictWrite").checked = data.write !== false;
  if ($("predictScopeMode")) $("predictScopeMode").value = data.scope_mode || "partial";
  if ($("predictSessionId")) $("predictSessionId").value = data.session_id || "";
  if ($("predictAllowUnfinalized")) $("predictAllowUnfinalized").checked = !!data.allow_unfinalized;
}

async function runPredict(button) {
  await withButtonLoading(button, "预测中...", async () => {
    startTask("predict", "生成后续剧情预测");
    const form = readPredictForm(false);
    const data = await api("/api/predict", {
      method: "POST",
      body: JSON.stringify({
        book: requireBook(),
        ...form,
        anchor_chapter: form.anchor_chapter ? Number(form.anchor_chapter) : null,
        anchor_chunk: form.anchor_chunk || null,
        session_id: form.session_id || state.currentSessionId || null,
      }),
    });
    await refreshAndOpenGeneratedDocument(data);
    finishTask(data);
    setActiveArtifact("predict", data);
    setResult(renderPredictionPacket(data));
    saveLocalState();
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
  if ($("contWrite")) $("contWrite").checked = data.write !== false;
  if ($("contLength")) $("contLength").value = data.length || "medium";
}

async function runContinue(button) {
  await withButtonLoading(button, "生成中...", async () => {
    startTask("continue", "生成续写任务包");
    const form = readContinueForm(false);
    if (form.after_chapter && form.after_chunk) throw new Error("接在章节后和接在 chunk 后只能填一个。");
    const data = await api("/api/continue", { method: "POST", body: JSON.stringify({ book: requireBook(), ...form }) });
    await refreshAndOpenGeneratedDocument(data);
    finishTask(data);
    setActiveArtifact("continue", data);
    setResult(renderContinuationPacket(data));
    saveLocalState();
  });
}

async function runEmbed(button) {
  await withButtonLoading(button, "构建中...", async () => {
    startTask("embedding", "构建语义索引");
    updateTaskProgress(0, "等待后端返回", "indeterminate");
    const data = await api("/api/embed", { method: "POST", body: JSON.stringify({ book: requireBook(), batch_size: Number($("embedBatch").value || 4), max_chars: Number($("embedMaxChars").value || 1500) }) });
    finishTask(data);
    setResult(card("语义索引构建完成", jsonDetails(data), "success-card"));
    await loadHealth().catch(() => {});
  });
}

async function loadDocuments() {
  const data = await api(`/api/documents?book=${encodeURIComponent(requireBook())}`);
  state.documents = data.documents || [];
  renderDocumentGroups();
  return data;
}

function renderDocumentGroups() {
  const list = $("documentList");
  const filter = $("documentCategoryFilter")?.value || "";
  const docs = (state.documents || []).filter((doc) => !filter || doc.category === filter);
  if (!docs.length) {
    list.innerHTML = `<div class="empty-state">暂无生成文档。</div>`;
    return;
  }
  const sorted = [...docs].sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
  const groups = {};
  for (const doc of sorted) {
    groups[doc.category] ||= [];
    groups[doc.category].push(doc);
  }
  list.innerHTML = Object.entries(groups).map(([category, groupDocs]) => `<section class="doc-group"><h3>${escapeHtml(translateDocumentCategory(category))}</h3>${groupDocs.map((doc, index) => `<button class="document-item ${index === 0 ? "recent" : ""}" data-path="${escapeHtml(doc.path)}"><strong>${escapeHtml(doc.name)}</strong><span>${escapeHtml(doc.path)} · ${formatSize(doc.size)}</span></button>`).join("")}</section>`).join("");
  list.querySelectorAll(".document-item").forEach((button) => {
    button.addEventListener("click", () => openDocument(button.dataset.path).catch(showError));
  });
}

function translateDocumentCategory(category) {
  return {
    maps: "剧情地图",
    reports: "分析报告",
    styles: "风格文档",
    continuations: "续写任务包",
    predictions: "剧情预测",
    summaries: "章节摘要",
  }[category] || category;
}

async function openDocument(path) {
  const data = await api(`/api/document?book=${encodeURIComponent(requireBook())}&path=${encodeURIComponent(path)}`);
  state.currentDocument = data;
  state.documentView = data.document.extension === ".json" ? "source" : "preview";
  renderDocument();
  setActiveArtifact("document", data, { title: `文档：${data.document.name}`, summary: data.document.path });
}

function renderDocument() {
  const output = $("documentOutput");
  if (!state.currentDocument) {
    $("documentTitle").textContent = "未选择文档";
    $("documentMeta").textContent = "点击文档后预览。";
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
  output.innerHTML = state.documentView === "source" || doc.extension !== ".md"
    ? `<pre class="code-box document-code">${escapeHtml(prettyContent(content, doc.extension))}</pre>`
    : `<div class="markdown-body">${renderMarkdown(content)}</div>`;
}

async function refreshAndOpenGeneratedDocument(data) {
  await loadDocuments().catch(() => {});
  const docs = data?.documents || [];
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

async function copyArtifactSummary() {
  if (!state.activeArtifact) return;
  await navigator.clipboard.writeText(state.activeArtifact.summary || JSON.stringify(state.activeArtifact.data, null, 2));
  showToast("产物摘要已复制。");
}

async function openArtifactDocument() {
  const doc = state.activeArtifact?.documents?.[0];
  if (!doc) throw new Error("当前产物没有关联文档。");
  await openDocument(doc.path);
}

function toggleArtifactJson() {
  if (!state.activeArtifact) return;
  setResult(jsonDetails(state.activeArtifact.data));
}

function attachCurrentArtifact() {
  if (!state.activeArtifact) throw new Error("当前没有产物。");
  setChatContext({ kind: "artifact", title: state.activeArtifact.title, data: state.activeArtifact.data });
}

function attachCurrentDocument() {
  if (!state.currentDocument) throw new Error("当前没有打开文档。");
  setChatContext({ kind: "document", title: state.currentDocument.document.path, data: state.currentDocument });
}

async function sendDocumentToClaude() {
  attachCurrentDocument();
  await sendClaudeMessage(`请阅读并基于当前文档继续处理：${state.currentDocument.document.path}`, {
    current_document: state.currentDocument,
    active_artifact: state.activeArtifact,
  });
}

function bind() {
  const on = (id, fn) => $(id)?.addEventListener("click", () => fn().catch((error) => { failTask(error); showError(error); }));
  on("refreshBooks", () => loadBooks());
  on("healthBtn", () => loadHealth());
  on("ingestBtn", () => ingestBook($("ingestBtn")));
  on("createSurveyBtn", () => createReadingSession("survey", $("createSurveyBtn")));
  on("createBalancedBtn", () => createReadingSession("balanced", $("createBalancedBtn")));
  on("createDeepBtn", () => createReadingSession("deep", $("createDeepBtn")));
  on("readNextBtn", () => runReadNext($("readNextBtn")));
  on("submitNoteBtn", () => submitNote($("submitNoteBtn")));
  on("finalizeBtn", () => finalizeReading($("finalizeBtn")));
  on("autoSurveyBtn", () => startAutoReading($("autoSurveyBtn")));
  $("pauseAutoSurveyBtn")?.addEventListener("click", pauseAutoSurveyReading);
  on("resumeAutoSurveyBtn", () => resumeAutoSurveyReading());
  on("readBtn", () => readText($("readBtn")));
  on("searchBtn", () => searchOrAsk("search", $("searchBtn")));
  on("askBtn", () => searchOrAsk("ask", $("askBtn")));
  document.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => runReport(button.dataset.action, button).catch((error) => { failTask(error); showError(error); }));
  });
  on("styleBtn", () => runStyle($("styleBtn")));
  on("predictBtn", () => runPredict($("predictBtn")));
  on("continueBtn", () => runContinue($("continueBtn")));
  on("embedBtn", () => runEmbed($("embedBtn")));
  on("sendClaudeBtn", () => sendClaudeMessage($("claudeMessage").value, state.chatContext || { active_artifact: state.activeArtifact, current_document: state.currentDocument }));
  on("sendArtifactBtn", () => sendArtifactToClaude());
  on("openArtifactDocBtn", () => openArtifactDocument());
  on("copyArtifactSummaryBtn", () => copyArtifactSummary());
  $("toggleArtifactJsonBtn").addEventListener("click", toggleArtifactJson);
  $("attachArtifactBtn").addEventListener("click", () => { try { attachCurrentArtifact(); } catch (error) { showError(error); } });
  $("attachDocumentBtn").addEventListener("click", () => { try { attachCurrentDocument(); } catch (error) { showError(error); } });
  on("documentSendClaudeBtn", () => sendDocumentToClaude());
  on("refreshDocs", () => loadDocuments());
  $("documentCategoryFilter").addEventListener("change", () => { renderDocumentGroups(); saveLocalState(); });
  $("docPreviewBtn").addEventListener("click", () => { state.documentView = "preview"; renderDocument(); });
  $("docSourceBtn").addEventListener("click", () => { state.documentView = "source"; renderDocument(); });
}

async function init() {
  const saved = restoreLocalState();
  bind();
  renderActiveTask();
  renderTaskHistory();
  renderReadingProgress(null);
  renderArtifactActions();
  renderChatMessages();
  await loadHealth().catch(showError);
  await loadBooks(saved.selectedBookId).catch(showError);
}

setInterval(() => {
  if (taskState.active?.status === "running") renderActiveTask();
}, 1000);

init();
