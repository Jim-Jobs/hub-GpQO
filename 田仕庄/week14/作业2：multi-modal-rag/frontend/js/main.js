import { checkHealth, setApiKey } from "./api.js";
import { initChat } from "./chat.js";
import { initDocuments, refreshDocuments } from "./documents.js";
import { initUpload } from "./upload.js";

const STORAGE_KB = "rag.knowledgeBase";
const STORAGE_KEY = "rag.apiKey";

const kbInput = document.getElementById("kb-input");
const apiKeyInput = document.getElementById("api-key-input");
const healthBadge = document.getElementById("health-badge");

const savedKb = window.localStorage.getItem(STORAGE_KB);
const savedKey = window.localStorage.getItem(STORAGE_KEY);
if (savedKb) kbInput.value = savedKb;
if (savedKey) apiKeyInput.value = savedKey;
setApiKey(apiKeyInput.value);

const getKb = () => kbInput.value.trim() || "default";

kbInput.addEventListener("change", () => {
  window.localStorage.setItem(STORAGE_KB, getKb());
  refreshDocuments();
});

apiKeyInput.addEventListener("input", () => {
  setApiKey(apiKeyInput.value);
  window.localStorage.setItem(STORAGE_KEY, apiKeyInput.value);
});

initUpload({
  getKb,
  onUploaded: () => refreshDocuments(),
});
initDocuments({ getKb });
initChat({ getKb });

refreshDocuments();

async function refreshHealth() {
  try {
    const health = await checkHealth();
    const healthy = health.status === "ok";
    healthBadge.textContent = health.mock ? "Mock 模式" : healthy ? "服务正常" : "依赖异常";
    healthBadge.className = `badge ${healthy ? "badge-done" : "badge-failed"}`;
    healthBadge.title = `DB ${health.db} · 解析 ${health.parse_backend}`;
  } catch {
    healthBadge.textContent = "服务离线";
    healthBadge.className = "badge badge-failed";
    healthBadge.title = "无法连接 /health";
  }
}

refreshHealth();
window.setInterval(refreshHealth, 30000);
