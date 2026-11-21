"""情感小助手前端：Streamlit 聊天界面，侧边栏切换 LangGraph / openai-agents 两个后端。"""
from datetime import datetime

import requests
import streamlit as st

ENGINES = {
    "LangGraph 版": "http://127.0.0.1:8101",
    "OpenAI Agents 版": "http://127.0.0.1:8102",
}

# 状态 Tag 四色惯例：正向绿、负面红、中性灰
TAGS = {
    "positive": ("正向", "green"),
    "negative": ("负面", "red"),
    "neutral": ("中性", "gray"),
}

st.set_page_config(page_title="情感小助手", page_icon="💬")
st.title("情感小助手")


def render_assistant(m: dict) -> None:
    label, color = TAGS[m["sentiment"]]
    st.badge(label, color=color)
    st.markdown(m["reply"])
    caption = m["time"] + (" · 规则兜底" if m["source"] == "mock" else "")
    st.caption(caption)


def call_chat(base_url: str, text: str) -> dict | None:
    try:
        resp = requests.post(f"{base_url}/api/chat", json={"text": text}, timeout=120)
        if resp.ok:
            return resp.json()
        st.error(f"后端返回错误 {resp.status_code}")
    except requests.RequestException:
        st.error("连不上后端，请检查侧边栏地址")
    return None


# ---- 侧边栏：引擎与后端地址 ----
with st.sidebar:
    st.radio("引擎", list(ENGINES), key="engine")
    if st.session_state.get("last_engine") != st.session_state.engine:
        st.session_state.last_engine = st.session_state.engine
        st.session_state.base_url = ENGINES[st.session_state.engine]
    base_url = st.text_input("后端地址", key="base_url").rstrip("/")

    try:
        r = requests.get(f"{base_url}/health", timeout=3)
        ok = r.ok and r.json().get("status") == "ok"
        llm_on = ok and r.json().get("llm_configured")
    except requests.RequestException:
        ok, llm_on = False, False
    if ok:
        st.badge("已连接", color="green")
        if not llm_on:
            st.caption("未配置模型，回答走规则兜底")
    else:
        st.badge("未连接", color="red")

# ---- 聊天记录 ----
if "messages" not in st.session_state:
    st.session_state.messages = []

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        if m["role"] == "user":
            st.markdown(m["text"])
        else:
            render_assistant(m)

# ---- 输入与请求 ----
if prompt := st.chat_input("说点什么吧…"):
    st.session_state.messages.append({"role": "user", "text": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        data = call_chat(base_url, prompt)
        if data:
            message = {
                "role": "assistant",
                "sentiment": data["sentiment"],
                "reply": data["reply"],
                "source": data["source"],
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            render_assistant(message)
            st.session_state.messages.append(message)
