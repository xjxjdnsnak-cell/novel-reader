const state = {
  books: [],
  currentBook: null,
  csrfToken: "",
  documents: [],
  currentDocument: null,
  documentView: "preview",
  lastAskPacket: null,
  lastStylePacket: null,
  lastContinuePacket: null,
};

const $ = (id) => document.getElementById(id);

function showToast(message) {
  const toast = $("toast");
  toast.textContent = message;
  toast.hidden = false;
  setTimeout(() => { toast.hidden = true; }, 3600);
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = { ...(options.headers || {}) };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    if (state.csrfToken) headers["X-Novel-Reader-Token"] = state.csrfToken;
  }
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({ ok: false, error: "响应不是 JSON" }));
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `请求失败：${response.status}`);
  }
  return data;
}

function requireBook() {
  if (!state.currentBook) {
    showToast("请先选择一本小说");
    throw new Error("No book selected");
  }
  return state.currentBook.book_id;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function card(title, meta, body, kind = "") {
  return `<article class="result-card ${kind}">
    <h3>${escapeHtml(title)}</h3>
    ${meta ? `<div class="result-meta">${escapeHtml(meta)}</div>` : ""}
    <div>${body}</div>
  </article>`;
}

function activateTab(name) {
  document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach((item) => item.classList.toggle("active", item.id === `tab-${name}`));
}

async function loadHealth() {
  const data = await api("/api/health");
  state.csrfToken = data.csrf_token || state.csrfToken;
  renderEmbeddingStatus(data.embedding || {});
  renderClaudeStatus(data.claude || {});
  $("healthBox").textContent = JSON.stringify(data, null, 2);
  $("claudeStatusBox").textContent = JSON.stringify(data.claude || {}, null, 2);
}

function renderEmbeddingStatus(emb) {
  const pill = $("embeddingPill");
  if (!emb.configured) {
    pill.textContent = "Embedding 未配置";
    pill.className = "pill warn";
  } else if (emb.available) {
    pill.textContent = "Embedding 可用";
    pill.className = "pill ok";
  } else {
    pill.textContent = "Embedding 不可用";
    pill.className = "pill bad";
  }
}

function renderClaudeStatus(status) {
  const pill = $("claudePill");
  if (!status.enabled) {
    pill.textContent = "Claude 未启用";
    pill.className = "pill warn";
  } else if (status.available) {
    pill.textContent = status.permission === "dangerous" ? "Claude 危险模式" : "Claude 可用";
    pill.className = status.permission === "dangerous" ? "pill bad" : "pill ok";
  } else {
    pill.textContent = "Claude 未找到";
    pill.className = "pill bad";
  }
}

async function loadBooks() {
  const data = await api("/api/books");
  state.books = data.books || [];
  const list = $("bookList");
  if (!state.books.length) {
    list.innerHTML = `<div class="result-meta">暂无书籍。请先导入 TXT/Markdown。</div>`;
    state.currentBook = null;
    $("currentTitle").textContent = "未选择";
    return;
  }
  list.innerHTML = state.books.map((book) => {
    const active = state.currentBook?.book_id === book.book_id ? " active" : "";
    const coverage = book.summary_coverage?.percent ?? 0;
    return `<div class="book-item${active}" data-book="${escapeHtml(book.book_id)}">
      <div class="book-title">${escapeHtml(book.title)}</div>
      <div class="book-meta">${escapeHtml(book.book_id)} · ${book.chapter_count} 章 · ${book.chunk_count} 块 · 摘要 ${coverage}%</div>
    </div>`;
  }).join("");
  list.querySelectorAll(".book-item").forEach((item) => {
    item.addEventListener("click", async () => {
      state.currentBook = state.books.find((book) => book.book_id === item.dataset.book);
      $("currentTitle").textContent = state.currentBook.title;
      await loadBooks();
      await loadStatus();
      await loadDocuments().catch(() => {});
    });
  });
  if (!state.currentBook) {
    state.currentBook = state.books[0];
    $("currentTitle").textContent = state.currentBook.title;
    await loadBooks();
    await loadStatus();
    await loadDocuments().catch(() => {});
  }
}

async function loadStatus() {
  const book = requireBook();
  const data = await api(`/api/status/${encodeURIComponent(book)}`);
  const coverage = data.summary_coverage?.percent ?? 0;
  $("coveragePill").textContent = `摘要覆盖 ${coverage}%`;
  $("coveragePill").className = coverage >= 100 ? "pill ok" : "pill warn";
  $("statusBox").textContent = JSON.stringify(data, null, 2);
}

async function ingestBook() {
  const payload = {
    path: $("ingestPath").value.trim(),
    book_id: $("ingestId").value.trim(),
    force: $("ingestForce").checked,
  };
  const data = await api("/api/ingest", { method: "POST", body: JSON.stringify(payload) });
  showToast(`导入成功：${data.book_id}`);
  state.currentBook = null;
  await loadBooks();
}

function renderChunks(chunks) {
  if (!chunks?.length) return `<div class="result-card warning">没有找到文本。</div>`;
  return chunks.map((chunk) => card(
    `${chunk.chunk_id} · 第 ${chunk.chapter_index} 章：${chunk.chapter_title}`,
    `行 ${chunk.line_start}-${chunk.line_end}`,
    `<pre class="code-box">${escapeHtml(chunk.text)}</pre>`
  )).join("");
}

async function readChapter() {
  const book = requireBook();
  const chapter = $("readChapter").value || "1";
  const data = await api(`/api/read?book=${encodeURIComponent(book)}&chapter=${encodeURIComponent(chapter)}`);
  $("readOutput").innerHTML = renderChunks(data.chunks);
}

async function readChunk() {
  const book = requireBook();
  const chunk = $("readChunk").value.trim();
  if (!chunk) return showToast("请输入 chunk id");
  const data = await api(`/api/read?book=${encodeURIComponent(book)}&chunk=${encodeURIComponent(chunk)}`);
  $("readOutput").innerHTML = renderChunks(data.chunks);
}

function renderEvidence(results) {
  if (!results?.length) return `<div class="result-card warning">没有找到证据。</div>`;
  return results.map((item) => card(
    `${item.chunk_id} · 第 ${item.chapter_index || item.chapter} 章：${item.chapter_title}`,
    `行 ${item.line_start}-${item.line_end} · ${item.source || "evidence"}`,
    escapeHtml(item.snippet || item.excerpt || "")
  )).join("");
}

async function search() {
  const book = requireBook();
  const payload = {
    book,
    query: $("searchQuery").value.trim(),
    top: Number($("searchTop").value || 8),
    semantic: $("searchSemantic").checked,
  };
  if (!payload.query) return showToast("请输入检索内容");
  const data = await api("/api/search", { method: "POST", body: JSON.stringify(payload) });
  $("searchOutput").innerHTML = renderEvidence(data.results);
}

async function ask() {
  const book = requireBook();
  const payload = {
    book,
    question: $("askQuestion").value.trim(),
    semantic: $("askSemantic").checked,
  };
  if (!payload.question) return showToast("请输入问题");
  const data = await api("/api/ask", { method: "POST", body: JSON.stringify(payload) });
  state.lastAskPacket = data;
  $("searchOutput").innerHTML = card("问答证据包", `覆盖率 ${data.summary_coverage_percent}%`, `<pre class="code-box">${escapeHtml(JSON.stringify(data, null, 2))}</pre>`);
}

async function runReport(action) {
  const book = requireBook();
  const data = await api(`/api/action/${action}`, { method: "POST", body: JSON.stringify({ book }) });
  $("reportsOutput").innerHTML = card("已生成", action, `<pre class="code-box">${escapeHtml(JSON.stringify(data, null, 2))}</pre>`, "good");
  await refreshAndOpenGeneratedDocument(data);
}

async function style() {
  const book = requireBook();
  const data = await api("/api/style", {
    method: "POST",
    body: JSON.stringify({ book, scene: $("styleScene").value, write: $("styleWrite").checked }),
  });
  state.lastStylePacket = data;
  const stats = data.corpus_stats || {};
  const scenes = (data.scene_profiles || []).map((scene) => card(
    `场景：${scene.scene}`,
    `匹配 ${scene.matched_chunks} 块`,
    renderEvidence(scene.evidence)
  )).join("");
  $("styleOutput").innerHTML = card(
    "全书风格统计",
    `覆盖率 ${data.summary_coverage_percent}%`,
    `<pre class="code-box">${escapeHtml(JSON.stringify(stats, null, 2))}</pre>`
  ) + scenes;
  await refreshAndOpenGeneratedDocument(data);
}

async function continuation() {
  const book = requireBook();
  const payload = {
    book,
    after_chapter: $("contChapter").value,
    after_chunk: $("contChunk").value.trim(),
    outline: $("contOutline").value.trim(),
    scene: $("contScene").value,
    semantic: $("contSemantic").checked,
    write: $("contWrite").checked,
    length: $("contLength").value,
  };
  if (payload.after_chapter && payload.after_chunk) {
    return showToast("章节和 chunk 只能选一个");
  }
  const data = await api("/api/continue", { method: "POST", body: JSON.stringify(payload) });
  state.lastContinuePacket = data;
  $("continueOutput").innerHTML = card("续写任务包", data.continuation_goal?.mode || "", `<pre class="code-box">${escapeHtml(JSON.stringify(data, null, 2))}</pre>`);
  await refreshAndOpenGeneratedDocument(data);
}

async function embed() {
  const book = requireBook();
  const payload = {
    book,
    batch_size: Number($("embedBatch").value || 4),
    max_chars: Number($("embedMaxChars").value || 1500),
  };
  $("embeddingOutput").innerHTML = card("Embedding", "运行中", "正在建立语义索引，请稍候。");
  const data = await api("/api/embed", { method: "POST", body: JSON.stringify(payload) });
  $("embeddingOutput").innerHTML = card("Embedding 完成", "", `<pre class="code-box">${escapeHtml(JSON.stringify(data, null, 2))}</pre>`, "good");
  await loadBooks();
}

async function loadDocuments() {
  const book = requireBook();
  const data = await api(`/api/documents?book=${encodeURIComponent(book)}`);
  state.documents = data.documents || [];
  renderDocumentList();
}

function renderDocumentList() {
  const list = $("documentList");
  if (!state.documents.length) {
    list.innerHTML = `<div class="result-meta">暂无生成文档。</div>`;
    return;
  }
  list.innerHTML = state.documents.map((doc) => {
    const active = state.currentDocument?.document?.path === doc.path ? " active" : "";
    return `<button class="document-item${active}" data-path="${escapeHtml(doc.path)}">
      <span>${escapeHtml(doc.name)}</span>
      <small>${escapeHtml(doc.category)} · ${formatSize(doc.size)}</small>
    </button>`;
  }).join("");
  list.querySelectorAll(".document-item").forEach((button) => {
    button.addEventListener("click", () => openDocument(button.dataset.path).catch(showError));
  });
}

async function openDocument(path) {
  const book = requireBook();
  const data = await api(`/api/document?book=${encodeURIComponent(book)}&path=${encodeURIComponent(path)}`);
  state.currentDocument = data;
  state.documentView = "preview";
  renderDocument();
  renderDocumentList();
  activateTab("documents");
}

function renderDocument() {
  const output = $("documentOutput");
  if (!state.currentDocument) {
    $("documentTitle").textContent = "未选择文档";
    $("documentMeta").textContent = "";
    $("docDownload").hidden = true;
    output.innerHTML = "";
    return;
  }
  const doc = state.currentDocument.document;
  $("documentTitle").textContent = doc.name;
  $("documentMeta").textContent = `${doc.path} · ${formatSize(doc.size)}`;
  $("docDownload").hidden = false;
  $("docDownload").href = `/api/document/download?book=${encodeURIComponent(doc.book_id)}&path=${encodeURIComponent(doc.path)}`;
  const content = state.currentDocument.content || "";
  if (state.documentView === "source" || doc.extension === ".json" || doc.extension === ".txt") {
    output.innerHTML = `<pre class="code-box document-code">${escapeHtml(prettyContent(content, doc.extension))}</pre>`;
  } else {
    output.innerHTML = `<div class="markdown-body">${renderMarkdown(content)}</div>`;
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

async function refreshAndOpenGeneratedDocument(data) {
  if (!data?.documents?.length) return;
  await loadDocuments().catch(() => {});
  const md = data.documents.find((doc) => doc.extension === ".md") || data.documents[0];
  if (md) await openDocument(md.path);
}

function formatSize(value) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

async function loadClaudeStatus() {
  const data = await api("/api/claude/status");
  renderClaudeStatus(data);
  $("claudeStatusBox").textContent = JSON.stringify(data, null, 2);
}

function buildClaudeContext() {
  const context = {};
  if ($("attachDocument").checked && state.currentDocument) {
    context.current_document = state.currentDocument;
  }
  if ($("attachAsk").checked && state.lastAskPacket) {
    context.ask_packet = state.lastAskPacket;
  }
  if ($("attachStyle").checked && state.lastStylePacket) {
    context.style_packet = state.lastStylePacket;
  }
  if ($("attachContinue").checked && state.lastContinuePacket) {
    context.continuation_packet = state.lastContinuePacket;
  }
  return context;
}

async function sendClaude() {
  const book = state.currentBook?.book_id || null;
  const message = $("claudeMessage").value.trim();
  if (!message) return showToast("请输入要发送给 Claude 的内容");
  const mode = $("claudeMode").value;
  appendChat("user", message);
  $("claudeMessage").value = "";
  appendChat("assistant", "Claude 正在处理...");
  const data = await api("/api/claude/chat", {
    method: "POST",
    body: JSON.stringify({ book, message, mode, context: buildClaudeContext() }),
  });
  const pending = $("chatLog").lastElementChild;
  if (pending) pending.remove();
  appendChat("assistant", data.reply || data.stdout || "(无输出)");
}

function appendChat(role, text) {
  const item = document.createElement("div");
  item.className = `chat-message ${role}`;
  item.innerHTML = `<div class="chat-role">${role === "user" ? "你" : "Claude"}</div><div class="chat-text">${escapeHtml(text)}</div>`;
  $("chatLog").appendChild(item);
  $("chatLog").scrollTop = $("chatLog").scrollHeight;
}

function bind() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => activateTab(tab.dataset.tab));
  });
  $("refreshBooks").addEventListener("click", () => loadBooks().catch(showError));
  $("healthBtn").addEventListener("click", () => loadHealth().catch(showError));
  $("statusBtn").addEventListener("click", () => loadStatus().catch(showError));
  $("ingestBtn").addEventListener("click", () => ingestBook().catch(showError));
  $("readBtn").addEventListener("click", () => readChapter().catch(showError));
  $("readChunkBtn").addEventListener("click", () => readChunk().catch(showError));
  $("searchBtn").addEventListener("click", () => search().catch(showError));
  $("askBtn").addEventListener("click", () => ask().catch(showError));
  document.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => runReport(button.dataset.action).catch(showError));
  });
  $("refreshDocs").addEventListener("click", () => loadDocuments().catch(showError));
  $("docPreviewBtn").addEventListener("click", () => { state.documentView = "preview"; renderDocument(); });
  $("docSourceBtn").addEventListener("click", () => { state.documentView = "source"; renderDocument(); });
  $("styleBtn").addEventListener("click", () => style().catch(showError));
  $("continueBtn").addEventListener("click", () => continuation().catch(showError));
  $("embedBtn").addEventListener("click", () => embed().catch(showError));
  $("claudeStatusBtn").addEventListener("click", () => loadClaudeStatus().catch(showError));
  $("sendClaudeBtn").addEventListener("click", () => sendClaude().catch(showError));
  $("clearChatBtn").addEventListener("click", () => { $("chatLog").innerHTML = ""; });
}

function showError(error) {
  showToast(error.message || String(error));
}

async function init() {
  bind();
  await loadHealth().catch(showError);
  await loadBooks().catch(showError);
}

init();
