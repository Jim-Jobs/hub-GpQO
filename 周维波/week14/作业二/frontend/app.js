/* 前端逻辑：文档状态（SSE + 轮询兜底）、批量上传、流式图文问答 */
(function () {
  "use strict";

  const state = {
    docs: [],
    attachment: null,
    sse: null,
    sseOk: false,
    sending: false,
  };

  const $ = (selector, root) => (root || document).querySelector(selector);
  const messagesEl = $("#messages");
  const docListEl = $("#doc-list");
  const questionEl = $("#question");
  const sendBtn = $("#btn-send");

  /* ------------------------------------------------------------------ */
  /* 工具函数                                                            */
  /* ------------------------------------------------------------------ */

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatSize(bytes) {
    if (!bytes) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit += 1;
    }
    return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
  }

  function formatDuration(ms) {
    if (!ms || ms < 0) return "";
    if (ms < 1000) return `${ms} ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`;
    return `${Math.round(ms / 60000)} 分钟`;
  }

  function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  async function api(path, options) {
    const response = await fetch(path, options);
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const data = await response.json();
        detail = data.detail || JSON.stringify(data);
      } catch (error) {
        /* ignore */
      }
      throw new Error(detail);
    }
    return response.json();
  }

  /* ------------------------------------------------------------------ */
  /* Markdown 渲染（本地实现，无外部依赖；重点是图片块级渲染）            */
  /* ------------------------------------------------------------------ */

  function renderInline(text) {
    return text
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*\w])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  }

  function renderTable(rows) {
    const cells = rows.map((row) =>
      row
        .replace(/^\||\|$/g, "")
        .split("|")
        .map((cell) => cell.trim())
    );
    if (!cells.length) return "";
    const head = cells[0];
    const body = cells.slice(1).filter((row) => !row.every((cell) => /^:?-{2,}:?$/.test(cell)));
    let html = "<table><thead><tr>";
    head.forEach((cell) => {
      html += `<th>${renderInline(cell)}</th>`;
    });
    html += "</tr></thead><tbody>";
    body.forEach((row) => {
      html += "<tr>";
      row.forEach((cell) => {
        html += `<td>${renderInline(cell)}</td>`;
      });
      html += "</tr>";
    });
    html += "</tbody></table>";
    return html;
  }

  function imageFigure(alt, url) {
    return `<figure class="md-img"><img src="${url}" alt="${alt}" loading="lazy" onerror="this.closest('figure').style.display='none'"><figcaption>${alt || ""}</figcaption></figure>`;
  }

  function renderMarkdown(markdown) {
    if (!markdown) return "";
    const codeBlocks = [];

    let text = String(markdown)
      .replace(/```([\w+-]*)\n([\s\S]*?)```/g, (match, lang, code) => {
        codeBlocks.push(`<pre><code>${escapeHtml(code.replace(/\n$/, ""))}</code></pre>`);
        return `\n\u0000CODE${codeBlocks.length - 1}\u0000\n`;
      })
      .replace(/\r\n/g, "\n");

    text = escapeHtml(text);

    const lines = text.split("\n");
    const out = [];
    let listType = null;

    const closeList = () => {
      if (listType) {
        out.push(`</${listType}>`);
        listType = null;
      }
    };

    for (let index = 0; index < lines.length; index += 1) {
      const trimmed = lines[index].trim();

      if (!trimmed) {
        closeList();
        continue;
      }

      const codeMatch = trimmed.match(/^\u0000CODE(\d+)\u0000$/);
      if (codeMatch) {
        closeList();
        out.push(codeBlocks[Number(codeMatch[1])] || "");
        continue;
      }

      const heading = trimmed.match(/^(#{1,4})\s+(.*)$/);
      if (heading) {
        closeList();
        const level = heading[1].length;
        out.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
        continue;
      }

      if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
        closeList();
        out.push("<hr>");
        continue;
      }

      const quote = trimmed.match(/^&gt;\s?(.*)$/);
      if (quote) {
        closeList();
        out.push(`<blockquote>${renderInline(quote[1])}</blockquote>`);
        continue;
      }

      if (trimmed.startsWith("|")) {
        closeList();
        const rows = [];
        while (index < lines.length && lines[index].trim().startsWith("|")) {
          rows.push(lines[index].trim());
          index += 1;
        }
        index -= 1;
        out.push(renderTable(rows));
        continue;
      }

      const imageOnly = trimmed.match(/^!\[([^\]]*)\]\(([^)\s]+)\)$/);
      if (imageOnly) {
        closeList();
        out.push(imageFigure(imageOnly[1], imageOnly[2]));
        continue;
      }

      const bullet = trimmed.match(/^[-*+]\s+(.*)$/);
      if (bullet) {
        if (listType !== "ul") {
          closeList();
          out.push("<ul>");
          listType = "ul";
        }
        out.push(`<li>${renderInline(bullet[1])}</li>`);
        continue;
      }

      const ordered = trimmed.match(/^\d+\.\s+(.*)$/);
      if (ordered) {
        if (listType !== "ol") {
          closeList();
          out.push("<ol>");
          listType = "ol";
        }
        out.push(`<li>${renderInline(ordered[1])}</li>`);
        continue;
      }

      closeList();
      const withInlineImages = trimmed.replace(
        /!\[([^\]]*)\]\(([^)\s]+)\)/g,
        (match, alt, url) => imageFigure(alt, url)
      );
      out.push(`<p>${renderInline(withInlineImages)}</p>`);
    }

    closeList();
    return out.join("\n");
  }

  /* ------------------------------------------------------------------ */
  /* 文档列表                                                            */
  /* ------------------------------------------------------------------ */

  function statusBadge(doc) {
    const cls = ["pending", "parsing", "completed", "failed"].includes(doc.status)
      ? doc.status
      : "pending";
    return `<span class="badge ${cls}">${escapeHtml(doc.status_label || doc.status)}</span>`;
  }

  function renderDocs() {
    if (!state.docs.length) {
      docListEl.innerHTML = '<div class="empty-docs">还没有文档，先上传一个 PDF 试试。</div>';
    } else {
      docListEl.innerHTML = state.docs
        .map((doc) => {
          const progress = doc.status === "completed" ? 100 : Math.max(0, Math.min(99, doc.progress || 0));
          const showBar = ["parsing", "pending"].includes(doc.status);
          const meta = [];
          meta.push(escapeHtml(doc.stage || "排队中"));
          if (doc.chunk_count) meta.push(`${doc.chunk_count} 切片`);
          if (doc.image_count) meta.push(`${doc.image_count} 图片`);
          if (doc.size_bytes) meta.push(formatSize(doc.size_bytes));
          if (doc.elapsed_ms) meta.push(`耗时 ${formatDuration(doc.elapsed_ms)}`);
          if (doc.attempts > 1) meta.push(`第 ${doc.attempts} 次尝试`);

          return `
            <article class="doc" data-id="${doc.id}">
              <div class="doc-head">
                <span class="doc-name" title="${escapeHtml(doc.filename)}">${escapeHtml(doc.filename)}</span>
                ${statusBadge(doc)}
              </div>
              ${showBar ? `<div class="progress"><div class="bar parsing" style="width:${progress}%"></div></div>` : ""}
              <div class="doc-meta"><span>${meta.join(" · ")}</span></div>
              ${doc.error ? `<div class="doc-error">${escapeHtml(doc.error)}</div>` : ""}
              <div class="doc-actions">
                <button type="button" data-action="reparse" data-id="${doc.id}">重新解析</button>
                <button type="button" class="danger" data-action="delete" data-id="${doc.id}">删除</button>
              </div>
            </article>`;
        })
        .join("");
    }

    const total = state.docs.length;
    const done = state.docs.filter((doc) => doc.status === "completed").length;
    const parsing = state.docs.filter((doc) => doc.status === "parsing").length;
    const pending = state.docs.filter((doc) => doc.status === "pending").length;
    const failed = state.docs.filter((doc) => doc.status === "failed").length;
    $("#doc-summary").textContent = `共 ${total} 篇 · 解析完成 ${done} · 解析中 ${parsing} · 待解析 ${pending}${
      failed ? ` · 失败 ${failed}` : ""
    }`;
    $("#ready-hint").textContent = done ? `可检索 ${done} 篇文档` : "暂无已解析文档";
  }

  function upsertDoc(doc) {
    if (!doc || !doc.id) return;
    const index = state.docs.findIndex((item) => item.id === doc.id);
    if (index >= 0) {
      state.docs[index] = doc;
    } else {
      state.docs.unshift(doc);
    }
    renderDocs();
  }

  async function loadDocuments() {
    try {
      const data = await api("/api/documents");
      state.docs = data.documents || [];
      renderDocs();
    } catch (error) {
      $("#doc-summary").textContent = `加载文档失败：${error.message}`;
    }
  }

  async function deleteDoc(id) {
    if (!window.confirm("删除该文档及其解析产物？")) return;
    try {
      await api(`/api/documents/${id}`, { method: "DELETE" });
      state.docs = state.docs.filter((doc) => doc.id !== id);
      renderDocs();
    } catch (error) {
      window.alert(`删除失败：${error.message}`);
    }
  }

  async function reparseDoc(id) {
    try {
      const data = await api(`/api/documents/${id}/reparse`, { method: "POST" });
      upsertDoc(data.document);
    } catch (error) {
      window.alert(`重新解析失败：${error.message}`);
    }
  }

  docListEl.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const id = button.getAttribute("data-id");
    if (button.dataset.action === "delete") deleteDoc(id);
    if (button.dataset.action === "reparse") reparseDoc(id);
  });

  /* ------------------------------------------------------------------ */
  /* 上传                                                                */
  /* ------------------------------------------------------------------ */

  const dropzone = $("#dropzone");
  const fileInput = $("#file-input");

  function setUploadStatus(message, isError) {
    const el = $("#upload-status");
    el.textContent = message || "";
    el.classList.toggle("hidden", !message);
    el.classList.toggle("error", Boolean(isError));
  }

  async function uploadFiles(fileList) {
    const files = Array.from(fileList || []).filter((file) =>
      file.name.toLowerCase().endsWith(".pdf")
    );
    if (!files.length) {
      setUploadStatus("请选择 PDF 文件", true);
      return;
    }

    const form = new FormData();
    files.forEach((file) => form.append("files", file));

    setUploadStatus(`正在上传 ${files.length} 个文件…`);
    try {
      const data = await api("/api/documents", { method: "POST", body: form });
      const accepted = (data.documents || []).length;
      const errors = data.errors || [];
      if (errors.length) {
        setUploadStatus(
          `已提交 ${accepted} 个，${errors.length} 个失败：${errors
            .map((item) => `${item.filename}（${item.error}）`)
            .join("；")}`,
          true
        );
      } else {
        setUploadStatus(`已提交 ${accepted} 个文档到 Kafka 解析队列`);
      }
      await loadDocuments();
    } catch (error) {
      setUploadStatus(`上传失败：${error.message}`, true);
    } finally {
      fileInput.value = "";
      setTimeout(() => setUploadStatus(""), 6000);
    }
  }

  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      fileInput.click();
    }
  });
  ["dragenter", "dragover"].forEach((name) =>
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.add("dragover");
    })
  );
  ["dragleave", "drop"].forEach((name) =>
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.remove("dragover");
    })
  );
  dropzone.addEventListener("drop", (event) => {
    if (event.dataTransfer && event.dataTransfer.files.length) {
      uploadFiles(event.dataTransfer.files);
    }
  });
  fileInput.addEventListener("change", () => uploadFiles(fileInput.files));

  /* ------------------------------------------------------------------ */
  /* 状态事件（SSE + 兜底轮询）                                          */
  /* ------------------------------------------------------------------ */

  function setSseState(ok) {
    state.sseOk = ok;
    const chip = $("#chip-kafka");
    const text = chip.querySelector("span");
    const dot = chip.querySelector(".dot");
    dot.className = `dot ${ok ? "ok" : "bad"}`;
    text.textContent = ok ? "Kafka 事件已连接" : "Kafka 事件重连中";
  }

  function connectEvents() {
    if (!window.EventSource) {
      setSseState(false);
      return;
    }
    if (state.sse) state.sse.close();
    const source = new EventSource("/api/events");
    state.sse = source;

    source.onopen = () => setSseState(true);
    source.onerror = () => setSseState(false);

    source.addEventListener("snapshot", (event) => {
      try {
        const data = JSON.parse(event.data);
        state.docs = data.documents || [];
        renderDocs();
        setSseState(true);
      } catch (error) {
        /* ignore */
      }
    });

    source.addEventListener("document_status", (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.document) upsertDoc(data.document);
      } catch (error) {
        /* ignore */
      }
    });
  }

  // 兜底：SSE 断开期间照旧轮询，保证状态最终一致
  setInterval(() => {
    if (!state.sseOk) loadDocuments();
  }, 5000);

  /* ------------------------------------------------------------------ */
  /* 系统信息                                                            */
  /* ------------------------------------------------------------------ */

  async function loadSystem() {
    try {
      const data = await api("/api/system");
      $("#chip-parser").textContent = `解析：${data.parser}${data.parser === "mineru" ? ` / ${data.mineru_backend}` : ""}`;
      $("#chip-llm").textContent = `模型：${data.llm_backend === "mock" ? "Mock（未配置 Key）" : data.llm_model}`;
      $("#chip-parser").title = `向量库 ${data.vector_store} · 向量模型 ${data.text_model}`;
      if (data.stats) {
        $("#ready-hint").textContent = data.stats.completed
          ? `可检索 ${data.stats.completed} 篇文档`
          : "暂无已解析文档";
      }
    } catch (error) {
      $("#chip-parser").textContent = "系统信息不可用";
    }
  }

  /* ------------------------------------------------------------------ */
  /* 聊天                                                                */
  /* ------------------------------------------------------------------ */

  function hideEmptyState() {
    const empty = $("#empty-state");
    if (empty) empty.remove();
  }

  function appendUserMessage(text, file) {
    const el = document.createElement("div");
    el.className = "msg user";
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text || "（仅图片）";
    if (file) {
      const img = document.createElement("img");
      img.src = URL.createObjectURL(file);
      img.alt = file.name;
      bubble.appendChild(img);
    }
    el.appendChild(bubble);
    messagesEl.appendChild(el);
    scrollToBottom();
  }

  function appendAssistant() {
    const el = document.createElement("div");
    el.className = "msg assistant";
    el.innerHTML = `
      <div class="msg-role"><span>助手</span><span class="typing"><i></i><i></i><i></i></span></div>
      <div class="msg-body md"></div>
      <div class="msg-sources"></div>
      <div class="msg-note-list"></div>`;
    messagesEl.appendChild(el);
    scrollToBottom();

    const roleEl = el.querySelector(".msg-role");
    const bodyEl = el.querySelector(".msg-body");
    const sourcesEl = el.querySelector(".msg-sources");
    const notesEl = el.querySelector(".msg-note-list");
    let content = "";
    let frame = 0;
    let typingEl = el.querySelector(".typing");

    const paint = () => {
      frame = 0;
      bodyEl.innerHTML = renderMarkdown(content);
      scrollToBottom();
    };

    return {
      append(text) {
        if (!text) return;
        content += text;
        if (!frame) frame = requestAnimationFrame(paint);
      },
      setMeta(meta) {
        if (meta && meta.answerer && meta.answerer.backend === "mock") {
          const badge = document.createElement("span");
          badge.className = "badge mock";
          badge.textContent = "Mock 模式";
          roleEl.appendChild(badge);
        }
        if (meta && Array.isArray(meta.sources) && meta.sources.length) {
          sourcesEl.innerHTML = meta.sources
            .slice(0, 8)
            .map((source) => {
              const page = source.page_no ? ` 第 ${source.page_no} 页` : "";
              const kind = source.kind === "image" ? " · 图像命中" : "";
              return `<span class="source-chip" title="${escapeHtml(source.snippet || "")}">
                <b>${escapeHtml(source.filename)}</b>${page}${kind}
                <span>${(source.score * 100).toFixed(0)}%</span>
              </span>`;
            })
            .join("");
        }
      },
      addNote(message) {
        if (!message) return;
        const note = document.createElement("div");
        note.className = "msg-note";
        note.textContent = message;
        notesEl.appendChild(note);
      },
      setError(message) {
        const error = document.createElement("div");
        error.className = "msg-error";
        error.textContent = message;
        notesEl.appendChild(error);
      },
      finish() {
        if (frame) {
          cancelAnimationFrame(frame);
          frame = 0;
        }
        paint();
        if (typingEl) {
          typingEl.remove();
          typingEl = null;
        }
      },
    };
  }

  async function consumeSSE(response, handlers) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);

        let eventName = "message";
        const dataLines = [];
        block.split("\n").forEach((line) => {
          if (line.startsWith("event:")) eventName = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
        });

        if (dataLines.length) {
          let payload = null;
          try {
            payload = JSON.parse(dataLines.join("\n"));
          } catch (error) {
            payload = null;
          }
          if (payload && handlers[eventName]) handlers[eventName](payload);
        }
        boundary = buffer.indexOf("\n\n");
      }
    }
  }

  async function sendMessage() {
    const text = questionEl.value.trim();
    if ((!text && !state.attachment) || state.sending) return;

    state.sending = true;
    sendBtn.disabled = true;
    hideEmptyState();

    const attachment = state.attachment;
    appendUserMessage(text, attachment);
    clearAttachment();
    questionEl.value = "";
    resizeTextarea();

    const assistant = appendAssistant();
    const form = new FormData();
    form.append("message", text);
    if (attachment) form.append("image", attachment);
    form.append("doc_ids", "");
    form.append("top_k", "0");

    try {
      const response = await fetch("/api/chat", { method: "POST", body: form });
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const data = await response.json();
          detail = data.detail || detail;
        } catch (error) {
          /* ignore */
        }
        throw new Error(detail);
      }
      await consumeSSE(response, {
        meta: (data) => assistant.setMeta(data),
        warning: (data) => assistant.addNote(data.message),
        delta: (data) => assistant.append(data.text),
        error: (data) => assistant.setError(data.message),
        done: () => {},
      });
    } catch (error) {
      assistant.setError(`请求失败：${error.message}`);
    } finally {
      assistant.finish();
      state.sending = false;
      sendBtn.disabled = false;
    }
  }

  $("#chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage();
  });

  questionEl.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });

  function resizeTextarea() {
    questionEl.style.height = "auto";
    questionEl.style.height = `${Math.min(questionEl.scrollHeight, 140)}px`;
  }

  questionEl.addEventListener("input", resizeTextarea);

  $("#btn-clear").addEventListener("click", () => {
    messagesEl.innerHTML = "";
    state.attachment = null;
    renderAttachment();
  });

  messagesEl.addEventListener("click", (event) => {
    const sample = event.target.closest(".sample");
    if (!sample) return;
    questionEl.value = sample.dataset.q || "";
    resizeTextarea();
    questionEl.focus();
  });

  /* ---------- 图片提问 ---------- */

  const imageInput = $("#image-input");
  const attachmentEl = $("#attachment");

  function renderAttachment() {
    if (!state.attachment) {
      attachmentEl.classList.add("hidden");
      return;
    }
    $("#attachment-thumb").src = URL.createObjectURL(state.attachment);
    $("#attachment-name").textContent = state.attachment.name;
    attachmentEl.classList.remove("hidden");
  }

  function clearAttachment() {
    state.attachment = null;
    imageInput.value = "";
    renderAttachment();
  }

  $("#btn-attach").addEventListener("click", () => imageInput.click());
  imageInput.addEventListener("change", () => {
    const file = imageInput.files && imageInput.files[0];
    if (!file) return;
    state.attachment = file;
    renderAttachment();
    questionEl.focus();
  });
  $("#btn-remove-image").addEventListener("click", clearAttachment);

  /* ------------------------------------------------------------------ */
  /* 启动                                                                */
  /* ------------------------------------------------------------------ */

  loadSystem();
  loadDocuments();
  connectEvents();
})();
