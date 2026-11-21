/**
 * POST /api/chat 的 SSE 客户端（week13 整壳复用，解析器不动）。
 *
 * 浏览器 EventSource 只支持 GET，因此用 fetch + ReadableStream 手动解析
 * SSE 文本（按空行分块，event:/data: 字段），onEvent 回调逐事件上抛。
 *
 * 本作业事件协议：
 *   meta      {thread_id}            会话 id（前端存 localStorage）
 *   sentiment {label, reason}        情感识别徽标
 *   delta     {text}                 回应 token（真流式，逐批到达）
 *   done      {}                     本轮结束
 *   error     {message}              下游失败降级
 */

/**
 * @param {string} text
 * @param {string|null} threadId
 * @param {(event: {event: string, data: any}) => void} onEvent
 * @param {AbortSignal} [signal]
 */
export async function chatStream(text, threadId, onEvent, signal) {
  const resp = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, thread_id: threadId }),
    signal,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`HTTP ${resp.status}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const handleBlock = (block) => {
    let event = "message";
    let data = "";
    for (const rawLine of block.split(/\r?\n/)) {
      if (rawLine.startsWith("event:")) event = rawLine.slice(6).trim();
      else if (rawLine.startsWith("data:")) data = rawLine.slice(5).trim();
    }
    if (!event) return;
    let parsed = data;
    try {
      parsed = data ? JSON.parse(data) : {};
    } catch {
      /* 保留原文 */
    }
    onEvent({ event, data: parsed });
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE 块分隔兼容 \n\n 与 \r\n\r\n（不同代理/客户端行尾不一致）
    let match;
    const sep = /\r?\n\r?\n/;
    while ((match = sep.exec(buffer))) {
      const block = buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0].length);
      if (block.trim()) handleBlock(block);
    }
  }
  if (buffer.trim()) handleBlock(buffer);
}
