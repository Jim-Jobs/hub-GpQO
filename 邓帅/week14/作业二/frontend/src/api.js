// 后端 API 封装：文档 CRUD + 多模态 SSE 问答
// EventSource 只支持 GET，SSE 走 fetch + ReadableStream 手动解析（POST /api/chat）

export async function listDocuments() {
  const r = await fetch("/api/documents");
  if (!r.ok) throw new Error(`列表失败 ${r.status}`);
  const j = await r.json();
  return j.documents || [];
}

export async function uploadDocuments(files) {
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  const r = await fetch("/api/documents", { method: "POST", body: fd });
  const j = await r.json().catch(() => ({}));
  if (!r.ok && r.status !== 207 && r.status !== 503) {
    throw new Error(j.detail || `上传失败 ${r.status}`);
  }
  return j;
}

export async function deleteDocument(id) {
  const r = await fetch(`/api/documents/${id}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`删除失败 ${r.status}`);
}

export async function retryDocument(id) {
  const r = await fetch(`/api/documents/${id}/retry`, { method: "POST" });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `重试失败 ${r.status}`);
  return j;
}

/**
 * SSE 流式问答。callbacks: { onRefs, onDelta, onDone, onError }
 * body: { history: [{role,content}], question, image?: base64 }
 */
export async function chatStream(body, callbacks) {
  const resp = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok || !resp.body) {
    callbacks.onError?.(`请求失败 ${resp.status}`);
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let event = "message";
  let data = "";

  const dispatch = () => {
    if (!data) return;
    let payload;
    try {
      payload = JSON.parse(data);
    } catch {
      payload = { raw: data };
    }
    if (event === "delta") callbacks.onDelta?.(payload.text || "");
    else if (event === "refs") callbacks.onRefs?.(payload);
    else if (event === "error") callbacks.onError?.(payload.message || "未知错误");
    else if (event === "done") callbacks.onDone?.(payload);
    event = "message";
    data = "";
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, idx).replace(/\r$/, "");
      buf = buf.slice(idx + 1);
      if (line === "") {
        dispatch();
      } else if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        data += line.slice(5).trim();
      }
    }
  }
  dispatch();
}
