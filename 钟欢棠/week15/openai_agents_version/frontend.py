"""openai-agents 版前端进程：Streamlit 用户输入页面。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests
import streamlit as st

st.set_page_config(page_title="openai-agents 情感响应助手", page_icon="🤖")
BACKEND_URL = st.sidebar.text_input("后端地址", value="http://localhost:8002")

st.title("openai-agents 情感响应助手")
st.caption("进程 1：情感识别 Agent　→　进程 2：鼓励 / 排解 Agent")

with st.form("chat_form", clear_on_submit=False):
    text = st.text_area("请输入你想说的话", height=140, placeholder="今天过得怎么样？")
    submitted = st.form_submit_button("发送给后端", type="primary")

if submitted:
    if not text.strip():
        st.warning("请输入内容后再发送")
    else:
        with st.spinner("识别情感并生成回应中…"):
            try:
                resp = requests.post(
                    f"{BACKEND_URL.rstrip('/')}/api/chat",
                    json={"message": text.strip()},
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                st.error(f"请求后端失败：{exc}")
                st.stop()

        label = {"positive": "正向", "negative": "负向", "neutral": "中性"}
        col1, col2 = st.columns(2)
        col1.metric("情感识别", label.get(data["sentiment"], data["sentiment"]))
        col2.metric("应对策略", data["strategy"])
        st.success(data["reply"])
        st.caption(
            f"置信度 {data['confidence'] * 100:.0f}% · {data['sentiment_reason']}"
        )
