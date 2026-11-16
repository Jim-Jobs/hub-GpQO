import { assetUrl, confirmChat, getChat, submitChat } from "../api/client.mjs";
import { chatStatusLabel, isChatTerminal } from "../api/status-labels.mjs";

const POLL_INTERVAL_MS = 1500;

/** 连接问答表单，显示阶段轮询、确认操作和图文混排结果。 */
export function initializeChat({ form, questionInput, imageInput, imagePreview, log }) {
  let previewUrl = null;
  form.addEventListener("submit", handleSubmit);
  imageInput.addEventListener("change", handleImageChange);

  /** 更新本地图片预览；图片文件对象会保留到确认流程结束。 */
  function handleImageChange() {
    const image = imageInput.files?.[0];
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      previewUrl = null;
    }
    imagePreview.replaceChildren();
    if (image) {
      previewUrl = URL.createObjectURL(image);
      const preview = document.createElement("img");
      preview.src = previewUrl;
      preview.alt = "待提交图片预览";
      imagePreview.append(preview);
    }
  }

  /** 立即呈现用户提问，再提交后台任务并开始按 chat_id 轮询。 */
  async function handleSubmit(event) {
    event.preventDefault();
    const questionText = questionInput.value.trim();
    const image = imageInput.files?.[0] ?? null;
    if (!questionText) {
      questionInput.focus();
      return;
    }

    const submitButton = form.querySelector("button[type='submit']");
    submitButton.disabled = true;
    appendUserMessage(log, questionText, image);
    const assistantMessage = createAssistantMessage();
    log.append(assistantMessage.article);
    updateAssistantStatus(assistantMessage, "正在提交问题…");

    try {
      const accepted = await submitChat(questionText, image);
      updateAssistantStatus(assistantMessage, "排队中");
      questionInput.value = "";
      imageInput.value = "";
      imagePreview.replaceChildren();
      if (previewUrl) {
        URL.revokeObjectURL(previewUrl);
        previewUrl = null;
      }
      // 后台轮询与表单提交解耦，用户可继续把后续问题加入 FIFO 队列。
      pollChat(accepted.chat_id, assistantMessage, questionText, image).catch(showPollingFailure);
    } catch (error) {
      updateAssistantStatus(assistantMessage, error.message, true);
    } finally {
      submitButton.disabled = false;
      questionInput.focus();
    }

    /** 将未预期的轮询异常写入对应助手消息，避免出现未处理 Promise。 */
    function showPollingFailure(error) {
      updateAssistantStatus(assistantMessage, error.message, true);
    }
  }
}

/** 轮询一个任务直至终态、等待确认或不可恢复的 404。 */
async function pollChat(chatId, message, questionText, image) {
  while (true) {
    await delay(POLL_INTERVAL_MS);
    let record;
    try {
      record = await getChat(chatId);
    } catch (error) {
      updateAssistantStatus(message, error.message, true);
      if (error.status === 404) {
        return;
      }
      await delay(POLL_INTERVAL_MS);
      continue;
    }

    updateAssistantStatus(message, chatStatusLabel(record));
    if (record.response) {
      renderResponse(message, record.response);
    }
    if (record.status === "needs_confirmation") {
      addConfirmationControls(message, chatId, questionText, image);
      return;
    }
    if (record.status === "failed") {
      updateAssistantStatus(message, record.error || "问答处理失败。", true);
      return;
    }
    if (record.status === "cancelled") {
      return;
    }
    if (isChatTerminal(record.status)) {
      return;
    }
  }
}

/** 添加继续通用回答和取消两个明确的用户选择。 */
function addConfirmationControls(message, chatId, questionText, image) {
  if (message.controls) {
    return;
  }
  const controls = document.createElement("div");
  controls.className = "confirmation-controls";
  const acceptButton = createButton("继续通用回答", "button button-primary");
  const cancelButton = createButton("取消", "button button-secondary");
  acceptButton.addEventListener("click", acceptGeneralAnswer);
  cancelButton.addEventListener("click", cancelAnswer);
  controls.append(acceptButton, cancelButton);
  message.content.append(controls);
  message.controls = controls;

  /** 用原 chat_id 重传原问题和图片，切换到通用回答路径。 */
  async function acceptGeneralAnswer() {
    await sendConfirmation(true);
  }

  /** 将等待确认任务标记为取消。 */
  async function cancelAnswer() {
    await sendConfirmation(false);
  }

  /** 提交确认决定，并在接受时继续轮询同一问答任务。 */
  async function sendConfirmation(acceptGeneral) {
    acceptButton.disabled = true;
    cancelButton.disabled = true;
    message.controls.remove();
    message.controls = null;
    updateAssistantStatus(message, acceptGeneral ? "已确认，正在排队…" : "正在取消…");
    try {
      await confirmChat(chatId, {
        acceptGeneralAnswer: acceptGeneral,
        questionText,
        image,
      });
      if (acceptGeneral) {
        await pollChat(chatId, message, questionText, image);
      } else {
        updateAssistantStatus(message, "已取消");
      }
    } catch (error) {
      updateAssistantStatus(message, error.message, true);
      if (error.status === 429 || error.status === 503) {
        addConfirmationControls(message, chatId, questionText, image);
      }
    }
  }
}

/** 将后端的有序 AnswerSegment 和来源安全地插入消息区域。 */
function renderResponse(message, response) {
  if (message.renderedResponse === response) {
    return;
  }
  message.renderedResponse = response;
  const answer = document.createElement("div");
  answer.className = "answer-content";
  for (const segment of response.answer_segments ?? []) {
    if (segment.type === "text" && typeof segment.text === "string") {
      const paragraph = document.createElement("p");
      paragraph.textContent = segment.text;
      answer.append(paragraph);
    } else if (segment.type === "image") {
      answer.append(createAssetFigure(segment));
    }
  }
  message.content.append(answer);

  if (response.citations?.length) {
    message.content.append(createCitationList(response.citations));
  }
}

/** 建立受控圖片與文件名/頁碼说明，不将服务端文本解释为 HTML。 */
function createAssetFigure(asset) {
  const figure = document.createElement("figure");
  figure.className = "answer-image";
  const image = document.createElement("img");
  image.src = assetUrl(asset.document_id, asset.asset_id);
  image.alt = `${asset.file_name} 第 ${asset.page_number ?? "未知"} 页图片`;
  image.loading = "lazy";
  const caption = document.createElement("figcaption");
  caption.textContent = `${asset.file_name} · 第 ${asset.page_number ?? "未知"} 页`;
  figure.append(image, caption);
  return figure;
}

/** 以列表显示经过后端核验的引用文件名和页码。 */
function createCitationList(citations) {
  const section = document.createElement("section");
  section.className = "citations";
  const heading = document.createElement("h4");
  heading.textContent = "资料来源";
  const list = document.createElement("ul");
  for (const citation of citations) {
    const item = document.createElement("li");
    item.textContent = `${citation.file_name} · 第 ${citation.page_number} 页`;
    list.append(item);
  }
  section.append(heading, list);
  return section;
}

/** 追加一个包含用户文字和可选本地图像预览的消息。 */
function appendUserMessage(log, questionText, imageFile) {
  clearEmptyState(log);
  const article = document.createElement("article");
  article.className = "message message-user";
  const label = document.createElement("h3");
  label.textContent = "你";
  const question = document.createElement("p");
  question.textContent = questionText;
  article.append(label, question);
  if (imageFile) {
    const image = document.createElement("img");
    image.className = "user-image-preview";
    const previewUrl = URL.createObjectURL(imageFile);
    image.src = previewUrl;
    image.alt = "你上传的图片";
    image.addEventListener("load", releasePreviewUrl, { once: true });
    image.addEventListener("error", releasePreviewUrl, { once: true });
    article.append(image);

    /** 图片加载完成或失败后释放临时对象 URL。 */
    function releasePreviewUrl() {
      URL.revokeObjectURL(previewUrl);
    }
  }
  log.append(article);
}

/** 有第一条真实消息时移除页面的空白引导状态。 */
function clearEmptyState(log) {
  log.querySelector(".empty-state")?.remove();
}

/** 创建助手消息的状态区和后续内容容器。 */
function createAssistantMessage() {
  const article = document.createElement("article");
  article.className = "message message-assistant";
  const label = document.createElement("h3");
  label.textContent = "资料库助手";
  const status = document.createElement("p");
  status.className = "chat-status";
  const content = document.createElement("div");
  content.className = "assistant-content";
  article.append(label, status, content);
  return { article, status, content, controls: null, renderedResponse: null };
}

/** 更新问答状态并区分可恢复的接口/任务错误。 */
function updateAssistantStatus(message, text, isError = false) {
  message.status.textContent = text;
  message.status.classList.toggle("message-error", isError);
}

/** 创建带样式的确认按钮。 */
function createButton(text, className) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = text;
  return button;
}

/** 等待固定间隔，使多任务轮询不会紧密占用后端接口。 */
function delay(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}
