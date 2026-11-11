import { askQuestion } from "./api.js";
import { showToast } from "./ui.js";

const MAX_IMAGES = 4;
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const ALLOWED_TYPES = ["image/png", "image/jpeg", "image/webp"];

const state = {
  busy: false,
  els: null,
  getKb: () => "default",
  images: [],
  seq: 0,
};

function isAllowedImage(file) {
  if (ALLOWED_TYPES.includes(file.type)) return true;
  return /\.(png|jpe?g|webp)$/i.test(file.name || "");
}

function addMessage(role, text, { loading = false, images = [] } = {}) {
  const { messages, empty } = state.els;
  if (empty) empty.hidden = true;

  const wrapper = document.createElement("div");
  wrapper.className = `chat-message is-${role}`;

  const roleElement = document.createElement("div");
  roleElement.className = "chat-role";
  roleElement.textContent = role === "user" ? "你" : "AI";

  const bubble = document.createElement("div");
  bubble.className = `chat-bubble${loading ? " chat-loading" : ""}`;
  bubble.textContent = text;

  if (images.length) {
    const box = document.createElement("div");
    box.className = "source-images";
    images.forEach((image) => {
      const element = document.createElement("img");
      element.src = image.url;
      element.alt = "用户上传图片";
      box.append(element);
    });
    bubble.append(box);
  }

  wrapper.append(roleElement, bubble);
  messages.append(wrapper);
  messages.scrollTop = messages.scrollHeight;

  return { wrapper, bubble };
}

function renderSources(bubble, sources) {
  if (!sources.length) return;

  const box = document.createElement("div");
  box.className = "chat-sources";

  const title = document.createElement("div");
  title.className = "chat-sources-title";
  title.textContent = "来源";
  box.append(title);

  sources.forEach((source, index) => {
    const item = document.createElement("div");
    item.className = "source-item";

    const indexElement = document.createElement("span");
    indexElement.className = "source-index";
    indexElement.textContent = String(index + 1);

    const body = document.createElement("div");
    const name = document.createElement("div");
    name.className = "source-name";
    const page = source.page ? ` · 第 ${source.page} 页` : "";
    name.textContent = `${source.file_name || "未知文件"}${page}`;

    const snippet = document.createElement("div");
    snippet.className = "source-snippet";
    snippet.textContent = (source.text_snippet || "").slice(0, 180);

    body.append(name, snippet);

    const images = Array.isArray(source.image_urls) ? source.image_urls : [];
    if (images.length) {
      const imageBox = document.createElement("div");
      imageBox.className = "source-images";
      images.slice(0, 3).forEach((url) => {
        const image = document.createElement("img");
        image.src = url;
        image.alt = "来源图片";
        image.loading = "lazy";
        imageBox.append(image);
      });
      body.append(imageBox);
    }

    item.append(indexElement, body);
    box.append(item);
  });

  bubble.append(box);
  state.els.messages.scrollTop = state.els.messages.scrollHeight;
}

function insertMarker(id) {
  const input = state.els.input;
  const marker = `![图${id}]`;
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? start;
  input.setRangeText(marker, start, end, "end");
  input.focus();
}

function renderAttachments() {
  const { attachments } = state.els;
  attachments.innerHTML = "";
  attachments.hidden = state.images.length === 0;

  state.images.forEach((image) => {
    const chip = document.createElement("div");
    chip.className = "attachment";

    const preview = document.createElement("img");
    preview.src = image.url;
    preview.alt = "待发送图片";

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "attachment-remove";
    remove.dataset.remove = String(image.id);
    remove.setAttribute("aria-label", "移除图片");
    remove.textContent = "×";

    chip.append(preview, remove);
    attachments.append(chip);
  });
}

function addImages(fileList) {
  for (const file of Array.from(fileList || [])) {
    if (state.images.length >= MAX_IMAGES) {
      showToast(`最多添加 ${MAX_IMAGES} 张图片`, "warning");
      break;
    }
    if (!isAllowedImage(file)) {
      showToast(`${file.name} 不是支持的图片格式`, "warning");
      continue;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      showToast(`${file.name} 超过 5MB`, "warning");
      continue;
    }
    const id = ++state.seq;
    state.images.push({
      id,
      file,
      url: URL.createObjectURL(file),
    });
    insertMarker(id);
  }
  renderAttachments();
}

function removeImage(id) {
  const image = state.images.find((item) => item.id === id);
  if (!image) return;
  state.images = state.images.filter((item) => item.id !== id);
  state.els.input.value = state.els.input.value
    .split(`![图${id}]`)
    .join("");
  URL.revokeObjectURL(image.url);
  renderAttachments();
}

async function send() {
  const question = state.els.input.value.trim();
  const images = state.images.slice();
  if ((!question && !images.length) || state.busy) {
    if (!state.busy) showToast("请输入问题或添加图片", "warning");
    return;
  }

  state.busy = true;
  state.els.send.disabled = true;
  state.els.imageBtn.disabled = true;
  state.els.input.value = "";
  state.images = [];
  renderAttachments();

  addMessage("user", question || "（仅图片提问）", { images });
  const loading = addMessage("assistant", "正在检索并生成…", {
    loading: true,
  });

  try {
    const data = await askQuestion({
      question,
      knowledgeBaseId: state.getKb(),
      images: images.map((image) => image.file),
    });
    loading.wrapper.remove();
    const message = addMessage("assistant", data.answer || "（空回答）");
    renderSources(message.bubble, Array.isArray(data.sources) ? data.sources : []);
  } catch (error) {
    loading.wrapper.remove();
    addMessage("assistant", `请求失败：${error.message || "未知错误"}`);
    showToast(error.message || "问答失败", "error");
  } finally {
    state.busy = false;
    state.els.send.disabled = false;
    state.els.imageBtn.disabled = false;
    state.els.input.focus();
  }
}

export function initChat({ getKb }) {
  state.getKb = getKb;
  state.els = {
    messages: document.getElementById("chat-messages"),
    empty: document.getElementById("chat-empty"),
    form: document.getElementById("chat-form"),
    input: document.getElementById("chat-input"),
    send: document.getElementById("chat-send"),
    imageBtn: document.getElementById("chat-image-btn"),
    imageInput: document.getElementById("chat-image-input"),
    attachments: document.getElementById("chat-attachments"),
  };

  state.els.form.addEventListener("submit", (event) => {
    event.preventDefault();
    send();
  });

  state.els.imageBtn.addEventListener("click", () => {
    state.els.imageInput.click();
  });
  state.els.imageInput.addEventListener("change", () => {
    addImages(state.els.imageInput.files);
    state.els.imageInput.value = "";
  });

  state.els.attachments.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove]");
    if (!button || state.busy) return;
    removeImage(Number(button.dataset.remove));
  });

  state.els.input.addEventListener("paste", (event) => {
    const files = Array.from(event.clipboardData?.files || []).filter((file) =>
      file.type.startsWith("image/"),
    );
    if (files.length) {
      event.preventDefault();
      addImages(files);
    }
  });

  state.els.form.addEventListener("dragover", (event) => {
    event.preventDefault();
  });
  state.els.form.addEventListener("drop", (event) => {
    event.preventDefault();
    if (event.dataTransfer?.files?.length) {
      addImages(event.dataTransfer.files);
    }
  });

  state.els.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send();
    }
  });
}
