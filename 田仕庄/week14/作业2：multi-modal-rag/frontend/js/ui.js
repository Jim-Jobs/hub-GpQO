const STATUS_META = {
  PENDING: { label: "待解析", cls: "pending" },
  PARSING: { label: "解析中", cls: "parsing" },
  DONE: { label: "解析完成", cls: "done" },
  FAILED: { label: "解析失败", cls: "failed" },
};

export function statusMeta(status, fallbackLabel = "") {
  const meta = STATUS_META[status] || { label: fallbackLabel || status || "未知", cls: "neutral" };
  return meta;
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function formatBytes(value) {
  const size = Number(value);
  if (!Number.isFinite(size) || size < 0) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let index = 0;
  let current = size;
  while (current >= 1024 && index < units.length - 1) {
    current /= 1024;
    index += 1;
  }
  return `${current.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export function formatDuration(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0) return "—";
  if (value < 60) return `${Math.round(value)}s`;
  const minutes = Math.floor(value / 60);
  const rest = Math.round(value % 60);
  if (minutes < 60) return `${minutes}m ${rest}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

export function parseUtc(value) {
  if (!value) return null;
  const text = String(value);
  const hasZone = /(?:z|[+-]\d{2}:?\d{2})$/i.test(text);
  const date = new Date(hasZone ? text : `${text}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(value) {
  const date = parseUtc(value);
  if (!date) return "—";
  return date.toLocaleString("zh-CN", { hour12: false });
}

let toastTimer = null;

export function showToast(message, kind = "info") {
  const element = document.getElementById("toast");
  if (!element) return;
  element.textContent = message;
  element.className = `toast is-${kind}`;
  element.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    element.hidden = true;
  }, 4000);
}
