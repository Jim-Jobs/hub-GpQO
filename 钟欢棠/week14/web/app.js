/* 多模态 RAG 前端：上传、状态轮询、Kafka 事件流、图文混排问答（无外部 CDN 依赖） */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[ch]);

  let sessionId = localStorage.getItem('mmrag.session');
  if (!sessionId) {
    sessionId = 's-' + Math.random().toString(36).slice(2, 10);
    localStorage.setItem('mmrag.session', sessionId);
  }

  const state = { files: [], image: null, imageUrl: null, busy: false, docs: [] };

  async function api(path, options = {}) {
    const response = await fetch(path, options);
    let data = {};
    try { data = await response.json(); } catch (_) { /* 忽略非 JSON 响应 */ }
    if (!response.ok) throw new Error(data.detail || data.message || `HTTP ${response.status}`);
    return data;
  }

  function safeUrl(url) {
    const value = String(url || '').trim();
    if (/^\/media\//.test(value) || /^https?:\/\//i.test(value) || /^blob:/.test(value) ||
        /^data:image\/(png|jpe?g|webp|gif);base64,/i.test(value)) return value;
    return '';
  }

  function inline(text) {
    let result = text;
    result = result.replace(/`([^`]+)`/g, (_m, code) => `<code>${code}</code>`);
    result = result.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    result = result.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (match, alt, url) => {
      const safe = safeUrl(url);
      return safe ? `<img class="md-img" src="${safe}" alt="${alt}" loading="lazy">` : match;
    });
    result = result.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (match, label, url) => {
      const safe = safeUrl(url);
      return safe ? `<a href="${safe}" target="_blank" rel="noopener">${label}</a>` : match;
    });
    return result;
  }

  /** 轻量 Markdown 渲染：先转义 HTML，再处理标题/列表/引用/代码/图片，避免 XSS。 */
  function renderMarkdown(markdown) {
    const blocks = String(markdown || '').replace(/\r\n/g, '\n').split(/\n{2,}/);
    return blocks.map((block) => {
      const raw = block.trim();
      if (!raw) return '';
      if (raw.startsWith('```')) {
        const code = escapeHtml(raw.replace(/^```[a-zA-Z]*\n?/, '').replace(/```$/, ''));
        return `<pre><code>${code}</code></pre>`;
      }
      const lines = escapeHtml(raw).split('\n').map((line) => {
        const heading = line.match(/^(#{1,4})\s+(.*)$/);
        if (heading) { const level = heading[1].length; return `<h${level}>${inline(heading[2])}</h${level}>`; }
        if (/^&gt;\s?/.test(line)) return `<blockquote>${inline(line.replace(/^&gt;\s?/, ''))}</blockquote>`;
        if (/^[-*]\s+/.test(line)) return `<div class="li">${inline(line.replace(/^[-*]\s+/, ''))}</div>`;
        if (/^\d+\.\s+/.test(line)) return `<div class="li">${inline(line)}</div>`;
        return inline(line);
      });
      return `<div class="block">${lines.join('<br>')}</div>`;
    }).join('');
  }

  function renderAnswer(data) {
    let html = `<div class="md">${renderMarkdown(data.answer)}</div>`;
    if (data.mode) html += `<div class="mode">生成方式：${escapeHtml(data.mode)}${data.error ? ' · ' + escapeHtml(data.error) : ''}</div>`;
    if (data.sources && data.sources.length) {
      html += '<div class="sources">' + data.sources.map((src) =>
        `<span class="source-chip" title="${escapeHtml(src.snippet)}">[${escapeHtml(src.index)}] ${escapeHtml(src.filename)} · P${escapeHtml(src.page || '?')}</span>`
      ).join('') + '</div>';
    }
    if (data.images && data.images.length) {
      html += '<div class="gallery">' + data.images.map((url) => {
        const safe = safeUrl(url);
        return safe ? `<a href="${safe}" target="_blank" rel="noopener"><img src="${safe}" alt="引用图片" loading="lazy"></a>` : '';
      }).join('') + '</div>';
    }
    return html;
  }

  function addMessage(role, html, images = []) {
    const chat = $('chat');
    const welcome = chat.querySelector('.welcome');
    if (welcome) welcome.remove();
    const wrap = document.createElement('div');
    wrap.className = `msg ${role}`;
    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = role === 'user' ? '我' : 'AI';
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    images.forEach((url) => {
      const safe = safeUrl(url);
      if (!safe) return;
      const link = document.createElement('a');
      link.href = safe; link.target = '_blank'; link.rel = 'noopener';
      const img = document.createElement('img');
      img.className = 'user-upload'; img.src = safe; img.alt = '上传的图片';
      link.appendChild(img); bubble.appendChild(link);
    });
    const content = document.createElement('div');
    content.innerHTML = html;
    bubble.appendChild(content);
    wrap.append(avatar, bubble);
    chat.appendChild(wrap);
    chat.scrollTop = chat.scrollHeight;
    return {
      setContent(newHtml) { content.innerHTML = newHtml; chat.scrollTop = chat.scrollHeight; },
    };
  }

  const WELCOME = `<div class="welcome"><b>欢迎使用多模态 RAG 问答</b><br>
    先在左侧上传 PDF，等待状态变成「解析完成」；<br>
    然后直接提问，或点「＋ 图片提问」把图片和问题一起送给大模型。<br>
    回答会图文混排展示知识库中的相关图片。</div>`;

  const BADGES = {
    '待解析': 'pending', '解析中': 'parsing', '解析完成': 'done', '解析失败': 'failed',
  };

  function renderDocs(docs) {
    state.docs = docs;
    const counts = { '待解析': 0, '解析中': 0, '解析完成': 0, '解析失败': 0 };
    docs.forEach((doc) => { counts[doc.status] = (counts[doc.status] || 0) + 1; });
    $('statusSummary').textContent =
      `待解析 ${counts['待解析']} · 解析中 ${counts['解析中']} · 解析完成 ${counts['解析完成']} · 解析失败 ${counts['解析失败']}`;

    if (!docs.length) {
      $('docList').innerHTML = '<div class="empty">还没有文档，先上传几个 PDF 吧。</div>';
    } else {
      $('docList').innerHTML = docs.map((doc) => {
        const id = escapeHtml(doc.id);
        const name = escapeHtml(doc.filename);
        const kind = BADGES[doc.status] || 'pending';
        const progress = `<div class="progress ${kind === 'parsing' ? 'running' : kind === 'done' ? 'finished' : ''}"><i style="${kind === 'done' ? '' : kind === 'running' ? '' : 'width:8%'}"></i></div>`;
        const meta = doc.status === '解析完成'
          ? `<span>${escapeHtml(doc.parser || '-')} 解析</span><span>${escapeHtml(doc.pages || 0)} 页</span><span>${escapeHtml(doc.chunk_count || 0)} 片段</span><span>${escapeHtml(doc.image_count || 0)} 图</span><a data-preview="${id}" data-name="${name}">预览内容</a>`
          : `<span>${((Number(doc.size) || 0) / 1024 / 1024).toFixed(2)} MB</span>`;
        return `<div class="doc-item">
          <div class="doc-row"><span class="doc-name" title="${name}">${name}</span><span class="badge ${kind}">${escapeHtml(doc.status)}</span></div>
          <div class="doc-meta">${meta}</div>
          ${doc.error ? `<div class="doc-error">${escapeHtml(doc.error)}</div>` : ''}
          ${progress}</div>`;
      }).join('');
    }

    const select = $('docScope');
    const previous = select.value;
    select.innerHTML = '<option value="">全部文档</option>' + docs
      .filter((doc) => doc.status === '解析完成')
      .map((doc) => `<option value="${escapeHtml(doc.id)}">${escapeHtml(doc.filename)}</option>`).join('');
    if ([...select.options].some((option) => option.value === previous)) select.value = previous;
  }

  function formatTime(ts) {
    const value = Number(ts);
    if (!Number.isFinite(value)) return '';
    return new Date(value * 1000).toLocaleTimeString('zh-CN', { hour12: false });
  }

  function renderEvents(events) {
    if (!events.length) {
      $('eventList').innerHTML = '<div class="empty">暂无事件，上传 PDF 或提问后会显示 Kafka 消息。</div>';
      return;
    }
    $('eventList').innerHTML = events.map((event) =>
      `<div class="event"><time>${formatTime(event.ts)}</time>
        <span class="src">${escapeHtml(event.source)}</span>
        <span class="act">${escapeHtml(event.action)}</span>
        <span class="detail" title="${escapeHtml(event.detail)}">${escapeHtml(event.detail)}</span></div>`
    ).join('');
  }

  function setPill(id, ok, text, neutral = false) {
    const pill = $(id);
    pill.textContent = text;
    pill.className = `pill ${ok ? 'ok' : neutral ? '' : 'bad'}`;
  }

  async function refreshDocs() {
    try { renderDocs((await api('/api/documents')).documents || []); } catch (_) { /* 服务未就绪 */ }
  }
  async function refreshEvents() {
    try { renderEvents((await api('/api/events?limit=25')).events || []); } catch (_) { /* 服务未就绪 */ }
  }
  async function refreshHealth() {
    try {
      const health = await api('/api/health');
      const local = health.mode === 'local';
      setPill('pillRedis', health.redis, local ? 'Redis 本地模拟' : `Redis ${health.redis ? '正常' : '断开'}`, local);
      setPill('pillKafka', health.kafka, local ? 'Kafka 本地模拟' : `Kafka ${health.kafka ? '正常' : '断开'}`, local);
      setPill('pillMineru', health.mineru_cli, `MinerU ${health.mineru_cli ? '已安装' : '降级 PyMuPDF'}`, true);
      setPill('pillLLM', health.llm, `LLM ${health.llm ? health.llm_model : '演示模式'}`, !health.llm);
    } catch (_) {
      ['pillRedis', 'pillKafka', 'pillMineru', 'pillLLM'].forEach((id) => setPill(id, false, '服务未就绪'));
    }
  }

  function renderQueue() {
    if (!state.files.length) { $('fileQueue').innerHTML = ''; return; }
    $('fileQueue').innerHTML = state.files.map((file) =>
      `<div class="queued">📄 <b>${escapeHtml(file.name)}</b> · ${(file.size / 1024 / 1024).toFixed(2)} MB</div>`
    ).join('');
  }

  async function uploadFiles() {
    if (state.busy) return;
    if (!state.files.length) { alert('请先选择 PDF 文件'); return; }
    const form = new FormData();
    state.files.forEach((file) => form.append('files', file, file.name));
    const button = $('uploadBtn');
    button.disabled = true; button.textContent = '上传中…';
    try {
      const result = await api('/api/upload', { method: 'POST', body: form });
      const failed = (result.documents || []).filter((doc) => doc.status === '解析失败');
      if (failed.length) console.warn('部分文件失败', failed);
      state.files = []; $('fileInput').value = ''; renderQueue();
      await Promise.all([refreshDocs(), refreshEvents()]);
    } catch (error) {
      alert('上传失败：' + error.message);
    } finally {
      button.disabled = false; button.textContent = '上传到 Kafka';
    }
  }

  function setImage(file) {
    if (state.imageUrl) URL.revokeObjectURL(state.imageUrl);
    state.image = file || null;
    state.imageUrl = file ? URL.createObjectURL(file) : null;
    const box = $('imagePreview');
    if (!state.imageUrl) { box.classList.add('hidden'); $('imageInput').value = ''; return; }
    $('previewImg').src = state.imageUrl;
    box.classList.remove('hidden');
  }

  async function pollQA(requestId, pending) {
    for (let attempt = 0; attempt < 150; attempt += 1) {
      await sleep(800);
      const result = await api(`/api/qa/${encodeURIComponent(requestId)}`);
      if (result.status === '完成' || result.status === '失败') return result;
      pending.setContent(`<span class="typing">${escapeHtml(result.status || '处理中')}</span>`);
    }
    throw new Error('等待回答超时（2 分钟）');
  }

  async function ask(event) {
    event.preventDefault();
    if (state.busy) return;
    const question = $('question').value.trim();
    if (!question) return;
    const form = new FormData();
    form.append('question', question);
    form.append('session_id', sessionId);
    form.append('doc_ids', $('docScope').value || '');
    form.append('use_vision', $('useVision').checked ? 'true' : 'false');
    if (state.image) form.append('image', state.image, state.image.name);

    addMessage('user', escapeHtml(question).replace(/\n/g, '<br>'), state.imageUrl ? [state.imageUrl] : []);
    const uploadedUrl = state.imageUrl;
    $('question').value = '';
    state.image = null; state.imageUrl = null; // object URL 仍由已插入的 img 使用
    $('imagePreview').classList.add('hidden'); $('imageInput').value = '';

    state.busy = true; $('askBtn').disabled = true;
    const pending = addMessage('assistant', '<span class="typing">已提交到 Kafka，等待 Worker 处理</span>');
    try {
      const submitted = await api('/api/qa', { method: 'POST', body: form });
      if (submitted.status === '失败') throw new Error(submitted.error || 'Kafka 发送失败');
      const result = await pollQA(submitted.request_id, pending);
      pending.setContent(renderAnswer(result));
    } catch (error) {
      pending.setContent(`<div class="error">请求失败：${escapeHtml(error.message)}</div>`);
    } finally {
      state.busy = false; $('askBtn').disabled = false;
      if (uploadedUrl) setTimeout(() => URL.revokeObjectURL(uploadedUrl), 600000);
    }
  }

  async function loadHistory() {
    try {
      const data = await api(`/api/history?session_id=${encodeURIComponent(sessionId)}`);
      $('chat').innerHTML = '';
      if (!data.messages.length) { $('chat').innerHTML = WELCOME; return; }
      data.messages.forEach((message) => {
        const html = message.role === 'assistant'
          ? renderAnswer({ answer: message.content, sources: [], images: [], mode: '' })
          : escapeHtml(message.content).replace(/\n/g, '<br>');
        addMessage(message.role, html, message.images || []);
      });
    } catch (_) { $('chat').innerHTML = WELCOME; }
  }

  async function previewDoc(docId, name) {
    try {
      const data = await api(`/api/documents/${encodeURIComponent(docId)}/content`);
      $('modalTitle').textContent = `解析内容预览 · ${name}`;
      $('modalBody').innerHTML = renderMarkdown(data.markdown);
      $('modal').classList.remove('hidden');
    } catch (error) { alert(error.message); }
  }

  function bind() {
    $('dropzone').addEventListener('click', () => $('fileInput').click());
    $('fileInput').addEventListener('change', (event) => {
      state.files = Array.from(event.target.files).filter((file) => file.name.toLowerCase().endsWith('.pdf'));
      renderQueue();
    });
    ['dragenter', 'dragover'].forEach((type) => $('dropzone').addEventListener(type, (event) => {
      event.preventDefault(); $('dropzone').classList.add('drag');
    }));
    ['dragleave', 'drop'].forEach((type) => $('dropzone').addEventListener(type, (event) => {
      event.preventDefault(); $('dropzone').classList.remove('drag');
    }));
    $('dropzone').addEventListener('drop', (event) => {
      state.files = Array.from(event.dataTransfer.files).filter((file) => file.name.toLowerCase().endsWith('.pdf'));
      renderQueue();
    });
    $('uploadBtn').addEventListener('click', uploadFiles);
    $('imageInput').addEventListener('change', (event) => setImage(event.target.files[0] || null));
    $('removeImage').addEventListener('click', () => setImage(null));
    $('qaForm').addEventListener('submit', ask);
    $('question').addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); ask(event); }
    });
    $('clearBtn').addEventListener('click', async () => {
      try {
        await api(`/api/history?session_id=${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
        $('chat').innerHTML = WELCOME;
      } catch (error) { alert(error.message); }
    });
    $('docList').addEventListener('click', (event) => {
      const link = event.target.closest('[data-preview]');
      if (link) previewDoc(link.dataset.preview, link.dataset.name);
    });
    $('modalClose').addEventListener('click', () => $('modal').classList.add('hidden'));
    $('modal').addEventListener('click', (event) => {
      if (event.target.id === 'modal') $('modal').classList.add('hidden');
    });
  }

  bind();
  Promise.all([refreshDocs(), refreshEvents(), refreshHealth(), loadHistory()]);
  setInterval(refreshDocs, 1500);
  setInterval(refreshEvents, 2000);
  setInterval(refreshHealth, 8000);
})();
