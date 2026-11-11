import { deleteDocument, listDocuments, reparseDocument } from "./api.js";
import {
  escapeHtml,
  formatBytes,
  formatDateTime,
  formatDuration,
  showToast,
  statusMeta,
} from "./ui.js";

const state = {
  items: [],
  total: 0,
  loading: false,
  failures: 0,
  timer: null,
  els: null,
  getKb: () => "default",
};

function rowHtml(item) {
  const meta = statusMeta(item.status, item.status_text);
  const canReparse = item.status === "DONE" || item.status === "FAILED";
  const error = item.error_message
    ? `<div class="error-text">${escapeHtml(item.error_message)}</div>`
    : "";

  return `
    <tr>
      <td>
        <div class="file-name" title="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</div>
        <div class="hint">${formatBytes(item.size_bytes)}</div>
      </td>
      <td>
        <span class="badge badge-${meta.cls}">${escapeHtml(item.status_text || meta.label)}</span>
        ${error}
      </td>
      <td>${item.page_count ?? "—"} / ${item.chunk_count ?? "—"}</td>
      <td>${formatDuration(item.elapsed_sec)}</td>
      <td>${formatDateTime(item.created_at)}</td>
      <td class="col-actions">
        <button type="button" class="link-btn" data-action="reparse" data-id="${item.file_id}" ${canReparse ? "" : "disabled"}>重新解析</button>
        <button type="button" class="link-btn danger" data-action="delete" data-id="${item.file_id}">删除</button>
      </td>
    </tr>
  `;
}

function render() {
  const { body, empty, subtitle } = state.els;
  const active = state.items.filter(
    (item) => item.status === "PENDING" || item.status === "PARSING",
  ).length;

  body.innerHTML = state.items.map(rowHtml).join("");
  empty.hidden = state.items.length > 0;
  subtitle.textContent = state.total
    ? `${state.total} 个文件${active ? `，${active} 个处理中` : ""}`
    : "暂无文档";

  schedule();
}

function schedule() {
  window.clearTimeout(state.timer);
  const active = state.items.some(
    (item) => item.status === "PENDING" || item.status === "PARSING",
  );
  if (!active || document.hidden) return;
  const delay = state.failures
    ? Math.min(2000 * (state.failures + 1), 10000)
    : 2000;
  state.timer = window.setTimeout(() => refreshDocuments({ silent: true }), delay);
}

export async function refreshDocuments({ silent = false } = {}) {
  if (state.loading) return;
  state.loading = true;
  try {
    const status = state.els?.filter?.value || "";
    const data = await listDocuments({
      knowledgeBaseId: state.getKb(),
      status,
    });
    state.items = Array.isArray(data?.items) ? data.items : [];
    state.total = data?.total ?? state.items.length;
    state.failures = 0;
    render();
  } catch (error) {
    state.failures += 1;
    if (!silent) showToast(error.message || "加载文档失败", "error");
    schedule();
  } finally {
    state.loading = false;
  }
}

async function handleAction(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const fileId = Number(button.dataset.id);
  const action = button.dataset.action;
  if (!fileId) return;

  if (action === "delete") {
    if (!window.confirm("删除该文件及其解析产物？此操作不可恢复。")) return;
    button.disabled = true;
    try {
      await deleteDocument(fileId);
      showToast("已删除", "success");
      await refreshDocuments({ silent: true });
    } catch (error) {
      showToast(error.message || "删除失败", "error");
      button.disabled = false;
    }
    return;
  }

  if (action === "reparse") {
    button.disabled = true;
    try {
      await reparseDocument(fileId);
      showToast("已重新加入解析队列", "success");
      await refreshDocuments({ silent: true });
    } catch (error) {
      showToast(error.message || "重新解析失败", "error");
      button.disabled = false;
    }
  }
}

export function initDocuments({ getKb }) {
  state.getKb = getKb;
  state.els = {
    body: document.getElementById("documents-body"),
    empty: document.getElementById("empty-state"),
    subtitle: document.getElementById("documents-subtitle"),
    filter: document.getElementById("status-filter"),
    refresh: document.getElementById("refresh-btn"),
  };

  state.els.filter.addEventListener("change", () => refreshDocuments());
  state.els.refresh.addEventListener("click", () => refreshDocuments());
  state.els.body.addEventListener("click", handleAction);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refreshDocuments({ silent: true });
  });
}
