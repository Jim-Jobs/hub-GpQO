"""后端自带的最小前端页面，便于 Docker 镜像启动后直接体验。"""


def build_index_page(title: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{title}</title>
<style>
  :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont,
    "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }}
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
    background: linear-gradient(135deg, #eef2ff, #fdf2f8); color: #1f2937; }}
  .card {{ width: min(680px, 92vw); background: #fff; border-radius: 20px;
    padding: 32px; box-shadow: 0 18px 50px rgba(79,70,229,.14); }}
  h1 {{ margin: 0 0 6px; font-size: 24px; }}
  .sub {{ margin: 0 0 24px; color: #6b7280; font-size: 14px; }}
  textarea {{ width: 100%; box-sizing: border-box; min-height: 120px; resize: vertical;
    padding: 14px; border: 1px solid #d1d5db; border-radius: 12px; font-size: 15px;
    outline: none; }}
  textarea:focus {{ border-color: #6366f1; box-shadow: 0 0 0 3px #e0e7ff; }}
  button {{ margin-top: 14px; padding: 12px 24px; border: 0; border-radius: 12px;
    background: #4f46e5; color: #fff; font-size: 15px; cursor: pointer; }}
  button:disabled {{ background: #a5b4fc; cursor: wait; }}
  .result {{ margin-top: 22px; padding: 18px; border-radius: 14px;
    background: #f8fafc; border: 1px solid #e5e7eb; display: none; }}
  .tags {{ display: flex; gap: 8px; margin-bottom: 10px; }}
  .tag {{ padding: 4px 10px; border-radius: 999px; font-size: 12px;
    background: #e0e7ff; color: #3730a3; }}
  .reply {{ line-height: 1.8; white-space: pre-wrap; }}
  .meta {{ margin-top: 10px; font-size: 12px; color: #9ca3af; }}
  .err {{ color: #dc2626; }}
</style>
</head>
<body>
  <main class="card">
    <h1>{title}</h1>
    <p class="sub">进程 1 识别情感 → 进程 2 正向鼓励 / 负面排解</p>
    <textarea id="message" placeholder="说说你此刻的心情……"></textarea>
    <br /><button id="send">发送</button>
    <section class="result" id="result">
      <div class="tags">
        <span class="tag" id="sentiment"></span>
        <span class="tag" id="strategy"></span>
      </div>
      <div class="reply" id="reply"></div>
      <div class="meta" id="meta"></div>
    </section>
    <p class="err" id="error"></p>
  </main>
<script>
const $ = (id) => document.getElementById(id);
const label = {{ positive: '正向', negative: '负向', neutral: '中性' }};
$('send').onclick = async () => {{
  const message = $('message').value.trim();
  if (!message) {{ $('error').textContent = '请先输入内容'; return; }}
  $('error').textContent = '';
  $('result').style.display = 'none';
  $('send').disabled = true;
  $('send').textContent = '处理中…';
  try {{
    const res = await fetch('/api/chat', {{
      method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ message }}),
    }});
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    const data = await res.json();
    $('sentiment').textContent = label[data.sentiment] || data.sentiment;
    $('strategy').textContent = data.strategy;
    $('reply').textContent = data.reply;
    $('meta').textContent = `置信度 ${{ (data.confidence * 100).toFixed(0) }}% · ${{ data.sentiment_reason }}`;
    $('result').style.display = 'block';
  }} catch (e) {{ $('error').textContent = '请求失败：' + e.message; }}
  finally {{ $('send').disabled = false; $('send').textContent = '发送'; }}
}};
</script>
</body>
</html>"""
