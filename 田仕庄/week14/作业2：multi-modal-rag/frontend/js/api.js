let apiKey = "";

export function setApiKey(value) {
  apiKey = String(value || "").trim();
}

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function authHeaders(extra = {}) {
  const headers = { ...extra };
  if (apiKey) headers["X-API-Key"] = apiKey;
  return headers;
}

async function readBody(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function errorMessage(body, status) {
  const detail = body && body.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  if (typeof body === "string" && body) return body;
  return `请求失败（${status}）`;
}

export async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: authHeaders(options.headers),
  });
  const body = await readBody(response);
  if (!response.ok) {
    throw new ApiError(errorMessage(body, response.status), response.status, body);
  }
  return body;
}

export function checkHealth() {
  return request("/health");
}

export function listDocuments({ knowledgeBaseId, status, limit = 200 } = {}) {
  const params = new URLSearchParams({
    knowledge_base_id: knowledgeBaseId || "default",
    limit: String(limit),
  });
  if (status) params.set("status", status);
  return request(`/documents?${params.toString()}`);
}

export function getStatuses(fileIds) {
  const params = new URLSearchParams({ file_ids: fileIds.join(",") });
  return request(`/documents/status?${params.toString()}`);
}

export function reparseDocument(fileId) {
  return request(`/documents/${fileId}/reparse`, { method: "POST" });
}

export function deleteDocument(fileId) {
  return request(`/documents/${fileId}`, { method: "DELETE" });
}

export function askQuestion({ question, knowledgeBaseId, images = [] }) {
  if (!images.length) {
    return request("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        knowledge_base_id: knowledgeBaseId || "default",
        use_clip: true,
      }),
    });
  }

  const form = new FormData();
  form.append("question", question);
  form.append("knowledge_base_id", knowledgeBaseId || "default");
  form.append("use_clip", "true");
  images.forEach((image) => form.append("images", image, image.name));

  return request("/chat", { method: "POST", body: form });
}

export function uploadDocuments({ files, knowledgeBaseId, onProgress }) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("knowledge_base_id", knowledgeBaseId || "default");
    files.forEach((file) => form.append("files", file, file.name));

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/upload/documents");
    if (apiKey) xhr.setRequestHeader("X-API-Key", apiKey);

    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(event.loaded / event.total);
      }
    });

    xhr.addEventListener("load", () => {
      let body = null;
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        body = xhr.responseText;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body);
        return;
      }
      reject(new ApiError(errorMessage(body, xhr.status), xhr.status, body));
    });

    xhr.addEventListener("error", () => {
      reject(new ApiError("网络错误，上传失败", 0, null));
    });

    xhr.send(form);
  });
}
