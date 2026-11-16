import { getDocument, uploadDocuments } from "../api/client.mjs";
import { documentStatusLabel, isDocumentTerminal } from "../api/status-labels.mjs";

const POLL_INTERVAL_MS = 1500;

/** 连接 PDF 上传表单，并为每个文档独立维护解析状态。 */
export function initializeDocuments({ form, fileInput, list, message }) {
  form.addEventListener("submit", handleUpload);

  /** 提交选中的多个 PDF，并保留服务端逐文件返回的记录。 */
  async function handleUpload(event) {
    event.preventDefault();
    const files = Array.from(fileInput.files ?? []);
    if (!files.length) {
      setMessage(message, "请选择一个或多个 PDF 文件。", true);
      return;
    }

    form.querySelector("button[type='submit']").disabled = true;
    setMessage(message, `正在上传 ${files.length} 个文件…`);
    try {
      const records = await uploadDocuments(files);
      for (const record of records) {
        const row = createDocumentRow(record);
        list.prepend(row);
        if (!isDocumentTerminal(record.status)) {
          pollDocument(record.document_id, row);
        }
      }
      setMessage(message, `已接收 ${records.length} 个文件；各文件解析状态会分别更新。`);
      fileInput.value = "";
    } catch (error) {
      setMessage(message, error.message, true);
    } finally {
      form.querySelector("button[type='submit']").disabled = false;
    }
  }
}

/** 创建一个可独立更新的文档状态行。 */
function createDocumentRow(record) {
  const row = document.createElement("li");
  row.className = "document-row";
  row.dataset.documentId = record.document_id;

  const heading = document.createElement("div");
  heading.className = "document-heading";
  const name = document.createElement("strong");
  name.textContent = record.file_name;
  const status = document.createElement("span");
  status.className = `status status-${record.status}`;
  status.textContent = documentStatusLabel(record);
  heading.append(name, status);

  const details = document.createElement("p");
  details.className = "document-details";
  details.textContent = record.error || record.summary || record.title || "等待后端处理";
  row.append(heading, details);
  return row;
}

/** 轮询单个 PDF；一个文档的失败不会影响列表中其他文档。 */
async function pollDocument(documentId, row) {
  const status = row.querySelector(".status");
  const details = row.querySelector(".document-details");
  while (true) {
    await delay(POLL_INTERVAL_MS);
    try {
      const record = await getDocument(documentId);
      status.className = `status status-${record.status}`;
      status.textContent = documentStatusLabel(record);
      details.textContent = record.error || record.summary || record.title || "正在处理…";
      if (isDocumentTerminal(record.status)) {
        return;
      }
    } catch (error) {
      details.textContent = `状态查询失败，正在重试：${error.message}`;
    }
  }
}

/** 在页面上显示上传结果或可恢复的网络错误。 */
function setMessage(element, text, isError = false) {
  element.textContent = text;
  element.classList.toggle("message-error", isError);
}

/** 等待固定轮询间隔，避免请求状态接口时形成紧密循环。 */
function delay(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}
