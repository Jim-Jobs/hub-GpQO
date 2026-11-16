const DOCUMENT_STATUS_LABELS = {
  pending: "等待解析",
  parsing: "解析中",
  completed: "解析完成",
  failed: "解析失败",
};

const CHAT_STAGE_LABELS = {
  scope_and_rewrite: "正在判断问题范围",
  retrieval: "正在检索资料",
  evidence_check: "正在核对证据",
  answer_generation: "正在生成回答",
  awaiting_confirmation: "等待你的确认",
};

/** 将单文件解析状态转换为用户可读文本。 */
export function documentStatusLabel(record) {
  const label = DOCUMENT_STATUS_LABELS[record.status] ?? "状态未知";
  return record.status === "pending" || record.status === "parsing"
    ? `${label} · ${record.progress ?? 0}%`
    : label;
}

/** 将排队、运行阶段和问答终态转换为用户可读文本。 */
export function chatStatusLabel(record) {
  if (record.status === "accepted") {
    return "排队中";
  }
  if (record.status === "running") {
    return CHAT_STAGE_LABELS[record.stage] ?? "处理中";
  }
  if (record.status === "needs_confirmation") {
    return "等待确认";
  }
  if (record.status === "completed") {
    return "回答完成";
  }
  if (record.status === "cancelled") {
    return "已取消";
  }
  if (record.status === "failed") {
    return "处理失败";
  }
  return "状态未知";
}

/** 判断文档状态是否已到不会继续变化的终态。 */
export function isDocumentTerminal(status) {
  return status === "completed" || status === "failed";
}

/** 判断问答状态是否无需继续轮询。 */
export function isChatTerminal(status) {
  return ["completed", "needs_confirmation", "cancelled", "failed"].includes(status);
}
