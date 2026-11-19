"""前端进程：Streamlit 聊天界面。

只做三件事：收集输入、调用后端、把「识别结果 + 回应 + 执行路径」展示出来。
识别结果单独展示，是为了让助教一眼看到进程 1 真的在工作，而不是只有一段回复。
"""

from __future__ import annotations

import os

import requests
import streamlit as st

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000").rstrip("/")

LABEL_STYLE = {
    "positive": ("😊 正面", "green"),
    "negative": ("😔 负面", "red"),
    "neutral": ("😐 中性", "gray"),
}

EXAMPLES = [
    "今天答辩通过了，导师还夸我思路清楚，太开心了！",
    "连续加班两周，项目还是被否了，感觉自己很没用。",
    "明天要去体检，有点紧张。",
    "随便聊聊，最近天气转凉了。",
]

st.set_page_config(page_title="情绪陪伴助手", page_icon="🌤️", layout="centered")
st.title("🌤️ 情绪陪伴助手")
st.caption("进程 1 识别情绪 → 进程 2 选择回应策略。可切换 LangGraph / OpenAI Agents SDK 两种编排。")

with st.sidebar:
    st.subheader("运行设置")
    engine = st.radio("编排引擎", ["langgraph", "agents"], horizontal=True)
    api_base = st.text_input("后端地址", API_BASE)

    health = None
    try:
        health = requests.get(f"{API_BASE}/api/health", timeout=3).json()
    except Exception:  # noqa: BLE001
        st.error("后端未启动，先运行：uvicorn app.main:app --reload")

    if health:
        mode = health.get("mode")
        if mode == "live":
            st.success(f"服务正常 · mode={mode}")
        else:
            st.info(f"离线演示模式（{mode}）：未配置 API Key，走规则引擎，链路完全一致。")
        st.caption(f"model: {health.get('model')} · default: {health.get('default_engine')}")

    st.divider()
    st.subheader("示例输入")
    for example in EXAMPLES:
        if st.button(example, use_container_width=True):
            st.session_state.pending = example

    if st.button("清空对话", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

st.session_state.setdefault("messages", [])
st.session_state.setdefault("pending", None)


def ask(text: str) -> dict | None:
    try:
        resp = requests.post(
            f"{API_BASE}/api/chat", json={"text": text, "engine": engine}, timeout=120
        )
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as exc:
        detail = exc.response.text[:300] if exc.response is not None else str(exc)
        st.error(f"请求失败：{detail}")
    except Exception as exc:  # noqa: BLE001
        st.error(f"无法连接后端：{exc}")
    return None


def render_assistant(payload: dict) -> None:
    sentiment = payload["sentiment"]
    label = sentiment["label"]
    text, color = LABEL_STYLE.get(label, (label, "gray"))

    st.markdown(payload["reply"])
    st.markdown(
        f":{color}[**{text}**] · 置信度 {sentiment['confidence']:.2f} · "
        f"`{payload['engine']}` · {payload['latency_ms']} ms · mode={payload['mode']}"
    )
    if payload.get("crisis"):
        st.warning("已触发安全兜底：该输入包含自伤相关表达，未走普通疏导流程。")

    with st.expander("查看进程 1 的判断依据 / 执行路径"):
        st.write(f"**理由**：{sentiment['reason']}")
        st.write(f"**关键词**：{'、'.join(sentiment.get('keywords') or []) or '无'}")
        st.write(f"**路径**：{' → '.join(payload.get('trail') or [])}")
        st.caption(f"trace_id: {payload.get('trace_id')}")


for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        if message["role"] == "user":
            st.markdown(message["content"])
        else:
            render_assistant(message["payload"])

prompt = st.chat_input("今天想聊点什么？")
if st.session_state.pending:
    prompt, st.session_state.pending = st.session_state.pending, None

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("进程 1 正在识别情绪…"):
            payload = ask(prompt)
        if payload:
            render_assistant(payload)
            st.session_state.messages.append({"role": "assistant", "payload": payload})
