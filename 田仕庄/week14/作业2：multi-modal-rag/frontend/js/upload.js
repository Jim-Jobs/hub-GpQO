import { uploadDocuments } from "./api.js";
import { escapeHtml, formatBytes, showToast } from "./ui.js";

const MAX_FILES = 20;
const MAX_BYTES = 50 * 1024 * 1024;

const state = {
  queue: [],
  seq: 0,
  uploading: false,
  els: null,
  getKb: () => "default",
  onUploaded: null,
};

function statusText(item) {
  switch (item.status) {
    case "uploading":
      return `上传中 ${Math.round((item.progress || 0) * 100)}%`;
    case "queued":
      return "已入队";
    case "duplicate":
      return "已存在";
    case "rejected":
      return "已拒绝";
    case "error":
      return "上传失败";
    default:
      return "待上传";
  }
}

function barPercent(item) {
  if (item.status === "uploading") return Math.round((item.progress || 0) * 100);
  if (["queued", "duplicate", "rejected", "error"].includes(item.status)) return 100;
  return 0;
}

function barClass(item) {
  if (["queued", "duplicate"].includes(item.status)) return "is-done";
  if (["rejected", "error"].includes(item.status)) return "is-error";
  return "";
}

function render() {
  const { queueEl, uploadBtn, clearBtn, summary } = state.els;

  queueEl.innerHTML = state.queue
    .map((item) => {
      const status = statusText(item);
      return `
        <li class="upload-item ${item.status === "error" ? "is-error" : ""}">
          <div class="upload-item-head">
            <span class="upload-item-name" title="${escapeHtml(item.file.name)}">${escapeHtml(item.file.name)}</span>
            <span class="upload-item-meta">${formatBytes(item.file.size)} · ${status}</span>
            <button type="button" class="upload-item-remove" data-remove="${item.id}" aria-label="移除 ${escapeHtml(item.file.name)}" ${state.uploading ? "disabled" : ""}>×</button>
          </div>
          <div class="progress">
            <div class="progress-bar ${barClass(item)}" data-progress="${item.id}" style="width:${barPercent(item)}%"></div>
          </div>
          ${item.error ? `<div class="error-text">${escapeHtml(item.error)}</div>` : ""}
        </li>
      `;
    })
    .join("");

  uploadBtn.disabled =
    state.uploading || !state.queue.some((item) => item.status === "ready");
  clearBtn.disabled = state.uploading || state.queue.length === 0;
  summary.textContent = state.uploading
    ? "上传中…"
    : state.queue.length
      ? `${state.queue.length} 个文件`
      : "";
}

function addFiles(fileList) {
  if (state.uploading) return;

  for (const file of Array.from(fileList)) {
    if (state.queue.length >= MAX_FILES) {
      showToast(`单次最多上传 ${MAX_FILES} 个文件`, "warning");
      break;
    }
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      showToast(`${file.name} 不是 PDF，已跳过`, "warning");
      continue;
    }
    if (file.size > MAX_BYTES) {
      showToast(`${file.name} 超过 50MB，已跳过`, "warning");
      continue;
    }
    state.queue.push({
      id: ++state.seq,
      file,
      status: "ready",
      progress: 0,
      error: "",
    });
  }
  render();
}

function updateProgress(items) {
  items.forEach((item) => {
    const bar = state.els.queueEl.querySelector(
      `[data-progress="${item.id}"]`,
    );
    if (bar) bar.style.width = `${Math.round((item.progress || 0) * 100)}%`;
  });
}

async function startUpload() {
  if (state.uploading) return;

  const ready = state.queue.filter((item) => item.status === "ready");
  if (!ready.length) return;

  state.uploading = true;
  ready.forEach((item) => {
    item.status = "uploading";
    item.progress = 0;
    item.error = "";
  });
  render();

  try {
    const result = await uploadDocuments({
      files: ready.map((item) => item.file),
      knowledgeBaseId: state.getKb(),
      onProgress: (progress) => {
        ready.forEach((item) => {
          if (item.status === "uploading") item.progress = progress;
        });
        updateProgress(ready);
      },
    });

    const items = Array.isArray(result?.items) ? result.items : [];
    items.forEach((item, index) => {
      const target = ready[index];
      if (!target) return;
      if (item.status === "REJECTED") {
        target.status = "rejected";
        target.error = item.error_message || "已拒绝";
      } else if (item.status === "DUPLICATE") {
        target.status = "duplicate";
        target.error = item.error_message || "文件已存在";
      } else if (item.status === "FAILED") {
        target.status = "error";
        target.error = item.error_message || "入队失败";
      } else {
        target.status = "queued";
        target.error = "";
      }
    });

    const accepted = result?.accepted ?? 0;
    const rejected = result?.rejected ?? 0;
    const duplicates = result?.duplicates ?? 0;
    ready
      .filter((item) => item.status === "uploading")
      .forEach((item) => {
        item.status = "error";
        item.error = "服务端未返回该文件的结果";
      });
    showToast(
      `上传完成：入队 ${accepted}，拒绝 ${rejected}，重复 ${duplicates}`,
      rejected || duplicates ? "warning" : "success",
    );
    state.onUploaded?.();
  } catch (error) {
    ready
      .filter((item) => item.status === "uploading")
      .forEach((item) => {
        item.status = "error";
        item.error = error.message || "上传失败";
      });
    showToast(error.message || "上传失败", "error");
  } finally {
    state.uploading = false;
    render();
  }
}

export function initUpload({ getKb, onUploaded }) {
  state.getKb = getKb;
  state.onUploaded = onUploaded;

  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("file-input");
  const queueEl = document.getElementById("upload-queue");
  const uploadBtn = document.getElementById("upload-btn");
  const clearBtn = document.getElementById("clear-btn");
  const summary = document.getElementById("upload-summary");

  state.els = { dropzone, queueEl, uploadBtn, clearBtn, summary };

  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      fileInput.click();
    }
  });

  fileInput.addEventListener("click", (event) => event.stopPropagation());
  fileInput.addEventListener("change", () => {
    addFiles(fileInput.files);
    fileInput.value = "";
  });

  let dragDepth = 0;
  dropzone.addEventListener("dragenter", (event) => {
    event.preventDefault();
    dragDepth += 1;
    dropzone.classList.add("is-dragover");
  });
  dropzone.addEventListener("dragover", (event) => event.preventDefault());
  dropzone.addEventListener("dragleave", (event) => {
    event.preventDefault();
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) dropzone.classList.remove("is-dragover");
  });
  dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    dragDepth = 0;
    dropzone.classList.remove("is-dragover");
    if (event.dataTransfer?.files?.length) addFiles(event.dataTransfer.files);
  });

  queueEl.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove]");
    if (!button || state.uploading) return;
    const id = Number(button.dataset.remove);
    state.queue = state.queue.filter((item) => item.id !== id);
    render();
  });

  uploadBtn.addEventListener("click", startUpload);
  clearBtn.addEventListener("click", () => {
    if (state.uploading) return;
    state.queue = [];
    render();
  });
}
