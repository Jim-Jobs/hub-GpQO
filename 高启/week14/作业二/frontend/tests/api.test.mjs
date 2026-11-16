import assert from "node:assert/strict";
import test from "node:test";

import {
  confirmChat,
  getChat,
  getDocument,
  submitChat,
  uploadDocuments,
} from "../src/api/client.mjs";
import { chatStatusLabel, documentStatusLabel } from "../src/api/status-labels.mjs";

/** 使用一次性 HTTP mock 验证请求路由、multipart 字段和响应映射。 */
function mockFetch(handler) {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = handler;
  return () => {
    globalThis.fetch = originalFetch;
  };
}

test("多 PDF 共用一次 multipart 上传，并保留各自状态", async function () {
  /** 检查批量文件字段，并返回模拟的逐文件状态。 */
  const restoreFetch = mockFetch(async (url, options) => {
    assert.equal(url, "/api/documents");
    const files = options.body.getAll("files");
    assert.deepEqual(files.map((file) => file.name), ["a.pdf", "b.pdf"]);
    return Response.json([
      { document_id: "doc-a", file_name: "a.pdf", status: "pending", progress: 0 },
      { document_id: "doc-b", file_name: "b.pdf", status: "failed", progress: 0 },
    ]);
  });

  try {
    const records = await uploadDocuments([
      new File(["a"], "a.pdf", { type: "application/pdf" }),
      new File(["b"], "b.pdf", { type: "application/pdf" }),
    ]);
    assert.deepEqual(records.map((record) => record.status), ["pending", "failed"]);
    assert.equal(documentStatusLabel(records[0]), "等待解析 · 0%");
    assert.equal(documentStatusLabel(records[1]), "解析失败");
  } finally {
    restoreFetch();
  }
});

test("问答状态标签覆盖排队、阶段、等待确认和终态", async function () {
  /** 返回指定状态的模拟 HTTP 响应。 */
  const statuses = [
    { chat_id: "chat-1", status: "accepted", stage: "scope_and_rewrite" },
    { chat_id: "chat-1", status: "running", stage: "retrieval" },
    { chat_id: "chat-1", status: "needs_confirmation", stage: "awaiting_confirmation" },
    { chat_id: "chat-1", status: "completed", stage: "answer_generation" },
  ];
  let index = 0;
  const restoreFetch = mockFetch(async (url) => {
    assert.equal(url, "/api/chat/chat-1");
    return Response.json(statuses[index++]);
  });

  try {
    const labels = [];
    for (let count = 0; count < statuses.length; count += 1) {
      labels.push(chatStatusLabel(await getChat("chat-1")));
    }
    assert.deepEqual(labels, ["排队中", "正在检索资料", "等待确认", "回答完成"]);
  } finally {
    restoreFetch();
  }
});

test("确认通用回答时沿用 chat_id 并重传原问题和图片", async function () {
  /** 验证确认 API 的 multipart 字段与目标 URL。 */
  const restoreFetch = mockFetch(async (url, options = {}) => {
    assert.equal(url, "/api/chat/chat-1/confirmation");
    assert.equal(options.body.get("accept_general_answer"), "true");
    assert.equal(options.body.get("question_text"), "原问题");
    assert.equal(options.body.get("image").name, "question.png");
    return Response.json({ chat_id: "chat-1", status: "accepted", stage: "answer_generation" });
  });

  try {
    const response = await confirmChat("chat-1", {
      acceptGeneralAnswer: true,
      questionText: "原问题",
      image: new File(["png"], "question.png", { type: "image/png" }),
    });
    assert.equal(response.chat_id, "chat-1");
  } finally {
    restoreFetch();
  }
});

test("文档轮询使用单文档接口", async function () {
  /** 返回某个文件进入解析中的状态。 */
  const restoreFetch = mockFetch(async (url, options = {}) => {
    assert.equal(url, "/api/documents/doc-1");
    assert.equal(options.method ?? "GET", "GET");
    return Response.json({
      document_id: "doc-1",
      file_name: "manual.pdf",
      status: "parsing",
      progress: 42,
    });
  });

  try {
    const record = await getDocument("doc-1");
    assert.equal(documentStatusLabel(record), "解析中 · 42%");
  } finally {
    restoreFetch();
  }
});

test("提交图文问题时发送文本和可选图片", async function () {
  /** 验证问答提交使用后端约定的 multipart 字段名。 */
  const restoreFetch = mockFetch(async (url, options) => {
    assert.equal(url, "/api/chat");
    assert.equal(options.method, "POST");
    assert.equal(options.body.get("question_text"), "这张图表示什么？");
    assert.equal(options.body.get("image").name, "chart.png");
    return Response.json({ chat_id: "chat-1", status: "accepted" });
  });

  try {
    const result = await submitChat(
      "这张图表示什么？",
      new File(["png"], "chart.png", { type: "image/png" }),
    );
    assert.equal(result.status, "accepted");
  } finally {
    restoreFetch();
  }
});
