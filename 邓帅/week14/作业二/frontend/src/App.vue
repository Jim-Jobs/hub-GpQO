<script setup>
import { computed, nextTick, onBeforeUnmount, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { marked } from "marked";
import {
  chatStream,
  deleteDocument,
  listDocuments,
  retryDocument,
  uploadDocuments,
} from "./api";

/* ==================== 文档管理 ==================== */

const docs = ref([]);
const fileInput = ref(null);
const uploading = ref(false);
const activeTab = ref("docs");
let pollTimer = null;

const STATUS_META = {
  pending: { label: "待解析", type: "info" },
  parsing: { label: "解析中", type: "warning" },
  done: { label: "解析完成", type: "success" },
  failed: { label: "解析失败", type: "danger" },
};

const hasActive = computed(() =>
  docs.value.some((d) => d.status === "pending" || d.status === "parsing")
);

async function refresh(silent = true) {
  try {
    docs.value = await listDocuments();
  } catch (e) {
    if (!silent) ElMessage.error(String(e.message || e));
  }
  schedulePoll();
}

// 有 pending/parsing 时每 2.5s 轮询，全静止即停
function schedulePoll() {
  clearTimeout(pollTimer);
  if (hasActive.value) {
    pollTimer = setTimeout(() => refresh(), 2500);
  }
}

async function onPickFiles() {
  fileInput.value?.click();
}

async function onFilesChange(e) {
  const files = [...e.target.files];
  e.target.value = "";
  if (!files.length) return;
  uploading.value = true;
  try {
    const res = await uploadDocuments(files);
    const ok = (res.documents || []).length;
    const bad = (res.errors || []).length;
    if (bad) ElMessage.warning(`上传 ${bad} 个失败（${res.errors[0].error || ""}）`);
    else if (ok) ElMessage.success(`已提交 ${ok} 个文件解析`);
    await refresh();
  } catch (err) {
    ElMessage.error(String(err.message || err));
  } finally {
    uploading.value = false;
  }
}

async function onRetry(row) {
  try {
    await retryDocument(row.id);
    ElMessage.success("已重新排队解析");
    await refresh();
  } catch (err) {
    ElMessage.error(String(err.message || err));
  }
}

async function onDelete(row) {
  try {
    await ElMessageBox.confirm(`删除「${row.filename}」及其全部解析产物与向量？`, "确认删除", {
      type: "warning",
    });
  } catch {
    return;
  }
  try {
    await deleteDocument(row.id);
    ElMessage.success("已删除");
    await refresh();
  } catch (err) {
    ElMessage.error(String(err.message || err));
  }
}

/* ==================== 多模态对话 ==================== */

const messages = ref([]); // {role, content, image?(dataUrl), refs?}
const question = ref("");
const pendingImage = ref(null); // { b64, dataUrl }
const chatInput = ref(null);
const chatBody = ref(null);
const streaming = ref(false);
const history = ref([]);

function renderMd(text) {
  return marked.parse(text || "", { breaks: true });
}

function onPickImage() {
  chatInput.value?.click();
}

function onImageChange(e) {
  const f = e.target.files?.[0];
  e.target.value = "";
  if (!f) return;
  const reader = new FileReader();
  reader.onload = () => {
    const dataUrl = String(reader.result);
    pendingImage.value = { b64: dataUrl.split(",")[1], dataUrl };
  };
  reader.readAsDataURL(f);
}

async function scrollToBottom() {
  await nextTick();
  if (chatBody.value) chatBody.value.scrollTop = chatBody.value.scrollHeight;
}

async function send() {
  const q = question.value.trim();
  if ((!q && !pendingImage.value) || streaming.value) return;
  const img = pendingImage.value;
  question.value = "";
  pendingImage.value = null;

  messages.value.push({
    role: "user",
    content: q || "（请看这张图）",
    image: img?.dataUrl,
  });
  const reply = { role: "assistant", content: "", refs: [] };
  messages.value.push(reply);
  streaming.value = true;
  await scrollToBottom();

  await chatStream(
    {
      history: history.value.slice(-10),
      question: q,
      image: img?.b64 || null,
    },
    {
      onRefs: (p) => {
        reply.refs = p.refs || [];
      },
      onDelta: async (t) => {
        reply.content += t;
        await scrollToBottom();
      },
      onError: (msg) => {
        reply.content += `\n\n> ⚠️ ${msg}`;
      },
      onDone: () => {},
    }
  );

  history.value.push({ role: "user", content: q || "（图片提问）" });
  history.value.push({ role: "assistant", content: reply.content });
  streaming.value = false;
  await scrollToBottom();
}

/* ==================== 生命周期 ==================== */

refresh(false);
onBeforeUnmount(() => clearTimeout(pollTimer));
</script>

<template>
  <el-container style="height: 100vh">
    <el-header height="54px" class="header">
      <b>week14 作业二 · Kafka 多模态 RAG 问答</b>
      <span class="sub">上传 PDF → Kafka 异步解析（MinerU）→ 多模态问答</span>
    </el-header>

    <el-main>
      <el-tabs v-model="activeTab" class="tabs">
        <!-- Tab 1: 文档管理 -->
        <el-tab-pane label="📄 文档管理" name="docs">
          <div class="toolbar">
            <input ref="fileInput" type="file" accept="application/pdf" multiple hidden @change="onFilesChange" />
            <el-button type="primary" :loading="uploading" @click="onPickFiles">上传 PDF（可多选）</el-button>
            <el-button @click="refresh(false)">刷新</el-button>
            <span class="sub" v-if="hasActive">存在解析中任务，每 2.5s 自动刷新…</span>
          </div>

          <el-table :data="docs" stripe>
            <el-table-column prop="id" label="ID" width="60" />
            <el-table-column prop="filename" label="文件名" min-width="220" show-overflow-tooltip />
            <el-table-column label="状态" width="110">
              <template #default="{ row }">
                <el-tag :type="STATUS_META[row.status]?.type || 'info'">
                  {{ STATUS_META[row.status]?.label || row.status }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="chunk_count" label="chunks" width="80" />
            <el-table-column label="错误信息" min-width="200">
              <template #default="{ row }">
                <el-tooltip v-if="row.error" :content="row.error" placement="top">
                  <span class="err">{{ row.error.slice(0, 60) }}</span>
                </el-tooltip>
                <span v-else class="sub">-</span>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="上传时间" width="170">
              <template #default="{ row }">{{ (row.created_at || "").replace("T", " ").slice(0, 19) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="160" fixed="right">
              <template #default="{ row }">
                <el-button
                  v-if="row.status === 'failed'"
                  size="small"
                  type="warning"
                  @click="onRetry(row)"
                >重试</el-button>
                <el-button size="small" type="danger" plain @click="onDelete(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <!-- Tab 2: 多模态对话 -->
        <el-tab-pane label="💬 多模态问答" name="chat">
          <div class="chat">
            <div ref="chatBody" class="chat-body">
              <div v-if="!messages.length" class="empty">
                上传并解析文档后，在这里提问。<br />支持纯文字提问，也可以附带一张图片（图问）。
              </div>
              <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
                <div class="bubble">
                  <img v-if="m.image" :src="m.image" class="user-img" />
                  <template v-if="m.role === 'assistant'">
                    <div class="md" v-html="renderMd(m.content)"></div>
                    <div v-if="m.refs && m.refs.length" class="refs">📎 引用：{{ m.refs.join("、") }}</div>
                  </template>
                  <template v-else>{{ m.content }}</template>
                </div>
              </div>
            </div>

            <div class="chat-input">
              <input ref="chatInput" type="file" accept="image/*" hidden @change="onImageChange" />
              <div v-if="pendingImage" class="img-chip">
                <img :src="pendingImage.dataUrl" />
                <el-button circle size="small" @click="pendingImage = null">✕</el-button>
              </div>
              <div class="input-row">
                <el-button circle @click="onPickImage" title="附带图片提问">🖼️</el-button>
                <el-input
                  v-model="question"
                  type="textarea"
                  :autosize="{ minRows: 1, maxRows: 4 }"
                  placeholder="提问（Enter 发送 / Shift+Enter 换行）"
                  @keydown.enter.exact.prevent="send"
                />
                <el-button type="primary" :loading="streaming" @click="send">发送</el-button>
              </div>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-main>
  </el-container>
</template>

<style>
body {
  margin: 0;
  font-family: -apple-system, "PingFang SC", sans-serif;
  background: #f5f7fa;
}
.header {
  display: flex;
  align-items: center;
  gap: 12px;
  background: #1f2d3d;
  color: #fff;
}
.header .sub {
  color: #9db4c0;
  font-size: 12px;
}
.sub {
  color: #909399;
  font-size: 12px;
}
.err {
  color: #f56c6c;
  font-size: 12px;
  cursor: help;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.tabs {
  background: #fff;
  padding: 0 18px;
  border-radius: 8px;
  min-height: calc(100vh - 54px - 32px);
}
/* 聊天 */
.chat {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 160px);
}
.chat-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 6px;
}
.empty {
  text-align: center;
  color: #909399;
  margin-top: 80px;
  line-height: 2;
}
.msg {
  display: flex;
  margin-bottom: 14px;
}
.msg.user {
  justify-content: flex-end;
}
.bubble {
  max-width: 76%;
  padding: 10px 14px;
  border-radius: 10px;
  background: #f0f2f5;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg.assistant .bubble {
  background: #fff;
  border: 1px solid #e4e7ed;
}
.user-img {
  max-width: 220px;
  border-radius: 6px;
  margin-bottom: 6px;
  display: block;
}
.md img {
  max-width: 100%;
  border-radius: 6px;
}
.md :deep(img) {
  max-width: 100%;
}
.refs {
  margin-top: 8px;
  font-size: 12px;
  color: #909399;
  border-top: 1px dashed #e4e7ed;
  padding-top: 6px;
}
.chat-input {
  border-top: 1px solid #ebeef5;
  padding-top: 10px;
}
.input-row {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}
.img-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}
.img-chip img {
  height: 56px;
  border-radius: 6px;
  border: 1px solid #e4e7ed;
}
</style>
