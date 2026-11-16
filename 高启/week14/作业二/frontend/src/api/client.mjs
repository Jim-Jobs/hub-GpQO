/** 将非成功 HTTP 响应转换为可直接展示的错误。 */
async function parseResponse(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof payload.detail === "string" ? payload.detail : "请求失败，请稍后重试";
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return payload;
}

/** 上传多个 PDF；浏览器负责生成 multipart boundary。 */
export async function uploadDocuments(files) {
  if (!files.length) {
    throw new Error("请先选择 PDF 文件");
  }
  const form = new FormData();
  for (const file of files) {
    form.append("files", file, file.name);
  }
  return parseResponse(await fetch("/api/documents", { method: "POST", body: form }));
}

/** 查询单个 PDF 的解析状态和摘要。 */
export async function getDocument(documentId) {
  return parseResponse(await fetch(`/api/documents/${encodeURIComponent(documentId)}`));
}

/** 提交文字和可选图片，并立即取得异步任务 ID。 */
export async function submitChat(questionText, image) {
  const form = new FormData();
  form.append("question_text", questionText);
  if (image) {
    form.append("image", image, image.name);
  }
  return parseResponse(await fetch("/api/chat", { method: "POST", body: form }));
}

/** 读取问答阶段或最终回答。 */
export async function getChat(chatId) {
  return parseResponse(await fetch(`/api/chat/${encodeURIComponent(chatId)}`));
}

/** 按后端契约取消等待确认，或携带原问题和图片继续通用回答。 */
export async function confirmChat(chatId, decision) {
  const form = new FormData();
  form.append("accept_general_answer", String(decision.acceptGeneralAnswer));
  if (decision.acceptGeneralAnswer) {
    form.append("question_text", decision.questionText);
    if (decision.image) {
      form.append("image", decision.image, decision.image.name);
    }
  }
  return parseResponse(
    await fetch(`/api/chat/${encodeURIComponent(chatId)}/confirmation`, {
      method: "POST",
      body: form,
    }),
  );
}

/** 构造由后端按文档和资源 ID 校验的图片 URL。 */
export function assetUrl(documentId, assetId) {
  return `/api/assets/${encodeURIComponent(documentId)}/${encodeURIComponent(assetId)}`;
}
