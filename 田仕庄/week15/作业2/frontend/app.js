const STORAGE_KEY = "sentiment-chat.conversation-id";
const MAX_INPUT_LENGTH = 4000;

const elements = {
  chatScroll: document.querySelector("#chat-scroll"),
  emptyState: document.querySelector("#empty-state"),
  messageList: document.querySelector("#message-list"),
  typingRow: document.querySelector("#typing-row"),
  form: document.querySelector("#chat-form"),
  input: document.querySelector("#message-input"),
  sendButton: document.querySelector("#send-button"),
  sendLabel: document.querySelector("#send-label"),
  charCount: document.querySelector("#char-count"),
  errorBanner: document.querySelector("#error-banner"),
  serviceState: document.querySelector("#service-state"),
  serviceText: document.querySelector("#service-text"),
  engineLabel: document.querySelector("#engine-label"),
  newChatButton: document.querySelector("#new-chat"),
  suggestions: document.querySelectorAll(".suggestion"),
};

const state = {
  conversationId: "",
  sending: false,
  loadingHistory: false,
};

let memoryConversationId = "";

class ApiError extends Error {
  constructor(code, message, status) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

function isUuidV4(value) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    value || "",
  );
}

function createConversationId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }

  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join("-");
}

function saveConversationId(value) {
  memoryConversationId = value;
  try {
    localStorage.setItem(STORAGE_KEY, value);
  } catch {
    // 隐私模式等场景下 localStorage 不可用，仅保留内存中的会话 ID。
  }
}

function readConversationId() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (isUuidV4(stored)) {
      return stored;
    }
  } catch {
    // 忽略存储不可用，使用内存或新生成的 ID。
  }
  return isUuidV4(memoryConversationId) ? memoryConversationId : "";
}

function ensureConversationId() {
  const existing = readConversationId();
  if (existing) {
    return existing;
  }
  const created = createConversationId();
  saveConversationId(created);
  return created;
}

async function requestJson(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(path, {
    ...options,
    headers,
    cache: "no-store",
  });

  const raw = await response.text();
  let payload = null;
  if (raw) {
    try {
      payload = JSON.parse(raw);
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    const error = payload && typeof payload === "object" ? payload.error : null;
    throw new ApiError(
      error?.code || `HTTP_${response.status}`,
      error?.message || "服务暂时不可用，请稍后重试。",
      response.status,
    );
  }

  if (!payload || typeof payload !== "object") {
    throw new ApiError("INVALID_RESPONSE", "服务返回了无法识别的数据。", response.status);
  }

  return payload;
}

function engineName(value) {
  if (value === "langgraph") {
    return "LangGraph";
  }
  if (value === "openai-agents") {
    return "OpenAI Agents";
  }
  return value || "未知引擎";
}

function setServiceState(status, text) {
  elements.serviceState.dataset.state = status;
  elements.serviceText.textContent = text;
}

async function refreshHealth() {
  try {
    const response = await fetch("/health", {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json().catch(() => null);

    if (payload?.engine) {
      elements.engineLabel.textContent = `引擎：${engineName(payload.engine)}`;
    }

    if (response.ok && payload?.status === "ok") {
      setServiceState("ok", `${engineName(payload.engine)} 已连接`);
      return;
    }

    setServiceState("error", payload?.db === "error" ? "数据库不可用" : "服务异常");
  } catch {
    setServiceState("error", "服务未连接");
  }
}

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }

  const now = new Date();
  const options =
    date.toDateString() === now.toDateString()
      ? { hour: "2-digit", minute: "2-digit", hour12: false }
      : { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false };
  return new Intl.DateTimeFormat("zh-CN", options).format(date);
}

function createAvatar() {
  const avatar = document.createElement("span");
  avatar.className = "avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = "心";
  return avatar;
}

function createBadge(sentiment) {
  if (sentiment !== "positive" && sentiment !== "negative") {
    return null;
  }

  const badge = document.createElement("span");
  badge.className = `badge badge-${sentiment}`;
  badge.textContent = sentiment === "positive" ? "正向" : "负面";
  badge.title = "情感识别结果";
  return badge;
}

function appendMessage(message, { animate = true, scroll = true } = {}) {
  const isUser = message.role === "user";
  const row = document.createElement("div");
  row.className = `message ${isUser ? "is-user" : "is-assistant"}`;
  if (!animate) {
    row.style.animation = "none";
  }

  const body = document.createElement("div");
  body.className = "message-body";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = message.content;
  body.append(bubble);

  const meta = document.createElement("div");
  meta.className = "message-meta";

  if (!isUser) {
    const badge = createBadge(message.sentiment);
    if (badge) {
      meta.append(badge);
    }
  }

  const timestamp = document.createElement("time");
  timestamp.textContent = formatTime(message.created_at);
  timestamp.dateTime = message.created_at || "";
  if (timestamp.textContent) {
    meta.append(timestamp);
  }

  if (meta.childElementCount > 0) {
    body.append(meta);
  }

  if (!isUser) {
    row.append(createAvatar());
  }
  row.append(body);
  elements.messageList.append(row);
  refreshEmptyState();

  if (scroll) {
    scrollToBottom();
  }
  return row;
}

function refreshEmptyState() {
  const hasMessages = elements.messageList.childElementCount > 0;
  elements.emptyState.hidden = hasMessages || state.sending;
}

function scrollToBottom(behavior = "smooth") {
  requestAnimationFrame(() => {
    elements.chatScroll.scrollTo({
      top: elements.chatScroll.scrollHeight,
      behavior,
    });
  });
}

function showTyping(visible) {
  elements.typingRow.hidden = !visible;
  if (visible) {
    scrollToBottom();
  }
}

function showError(message) {
  elements.errorBanner.textContent = message;
  elements.errorBanner.hidden = false;
}

function hideError() {
  elements.errorBanner.hidden = true;
  elements.errorBanner.textContent = "";
}

function describeError(error) {
  const friendly = {
    EMPTY_INPUT: "请先写下一点内容再发送。",
    INPUT_TOO_LONG: "输入内容过长，请精简后再试。",
    INVALID_CONVERSATION_ID: "会话标识异常，请新建对话后重试。",
    LLM_TIMEOUT: "模型响应超时，请稍后再试。",
    LLM_RATE_LIMITED: "请求有点频繁，请稍后再试。",
    LLM_UNAVAILABLE: "模型服务暂时不可用，请稍后再试。",
    VALIDATION_ERROR: "请求参数有误，请检查后重试。",
    CONVERSATION_NOT_FOUND: "会话不存在。",
    UNAUTHORIZED: "访问令牌无效，请检查部署配置。",
    INTERNAL_ERROR: "服务内部出现异常，请稍后再试。",
  };

  if (error instanceof ApiError) {
    if (friendly[error.code]) {
      return friendly[error.code];
    }
    return error.code ? `${error.message}（${error.code}）` : error.message;
  }

  return "网络连接失败，请确认服务已启动后重试。";
}

function resizeInput() {
  elements.input.style.height = "auto";
  if (elements.input.value.length === 0) {
    return;
  }
  elements.input.style.height = `${Math.min(elements.input.scrollHeight, 168)}px`;
}

function updateComposer() {
  const blocked = state.sending || state.loadingHistory;
  elements.input.disabled = blocked;
  elements.newChatButton.disabled = blocked;
  elements.sendButton.disabled = blocked || elements.input.value.trim().length === 0;
  elements.sendLabel.textContent = state.sending ? "回复中" : "发送";
  elements.form.setAttribute("aria-busy", String(state.sending));
}

function syncInput() {
  resizeInput();
  const length = elements.input.value.length;
  elements.charCount.textContent = `${length} / ${MAX_INPUT_LENGTH}`;
  elements.charCount.classList.toggle("is-near-limit", length > MAX_INPUT_LENGTH * 0.9);
  updateComposer();
}

async function loadHistory() {
  const conversationId = state.conversationId;
  state.loadingHistory = true;
  updateComposer();

  try {
    const payload = await requestJson(
      `/conversations/${conversationId}/messages?limit=50`,
    );
    if (conversationId !== state.conversationId) {
      return;
    }

    elements.messageList.replaceChildren();
    for (const message of payload.messages) {
      appendMessage(message, { animate: false, scroll: false });
    }
    refreshEmptyState();
    if (payload.messages.length > 0) {
      scrollToBottom("auto");
    }
  } catch (error) {
    if (!(error instanceof ApiError && error.status === 404)) {
      showError(`历史记录加载失败：${describeError(error)}`);
    }
  } finally {
    if (conversationId === state.conversationId) {
      state.loadingHistory = false;
      updateComposer();
      refreshEmptyState();
    }
  }
}

async function sendMessage() {
  if (state.sending || state.loadingHistory) {
    return;
  }

  const text = elements.input.value.trim();
  if (!text) {
    showError("请先写下一点内容再发送。");
    elements.input.focus();
    return;
  }
  if (text.length > MAX_INPUT_LENGTH) {
    showError(`输入内容不能超过 ${MAX_INPUT_LENGTH} 个字符。`);
    return;
  }

  state.sending = true;
  hideError();
  const optimistic = appendMessage({
    role: "user",
    content: text,
    sentiment: null,
    created_at: new Date().toISOString(),
  });
  elements.input.value = "";
  syncInput();
  showTyping(true);
  scrollToBottom();

  try {
    const payload = await requestJson("/chat", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: state.conversationId,
        message: text,
      }),
    });
    appendMessage({
      role: "assistant",
      content: payload.reply,
      sentiment: payload.sentiment,
      created_at: new Date().toISOString(),
    });
  } catch (error) {
    optimistic.remove();
    elements.input.value = text;
    showError(describeError(error));
    if (error instanceof ApiError && error.status >= 500) {
      void refreshHealth();
    }
  } finally {
    state.sending = false;
    showTyping(false);
    syncInput();
    refreshEmptyState();
    scrollToBottom();
    elements.input.focus();
    const length = elements.input.value.length;
    elements.input.setSelectionRange(length, length);
  }
}

function startNewConversation() {
  if (state.sending || state.loadingHistory) {
    return;
  }

  state.conversationId = createConversationId();
  saveConversationId(state.conversationId);
  elements.messageList.replaceChildren();
  elements.input.value = "";
  hideError();
  showTyping(false);
  syncInput();
  refreshEmptyState();
  elements.chatScroll.scrollTo({ top: 0, behavior: "smooth" });
  elements.input.focus();
}

function bindEvents() {
  elements.form.addEventListener("submit", (event) => {
    event.preventDefault();
    void sendMessage();
  });

  elements.input.addEventListener("input", syncInput);

  elements.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      elements.form.requestSubmit();
    }
  });

  for (const suggestion of elements.suggestions) {
    suggestion.addEventListener("click", () => {
      elements.input.value = suggestion.dataset.message || "";
      syncInput();
      elements.input.focus();
    });
  }

  elements.newChatButton.addEventListener("click", startNewConversation);

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      void refreshHealth();
    }
  });

  window.addEventListener("load", syncInput, { once: true });
}

function init() {
  state.conversationId = ensureConversationId();
  bindEvents();
  syncInput();
  refreshEmptyState();
  updateComposer();
  void refreshHealth();
  void loadHistory();
  elements.input.focus();
}

init();
