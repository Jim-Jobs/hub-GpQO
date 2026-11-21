"""
网络层（Network Layer）—— 后端 A：LangGraph 服务

- 初始化 FastAPI
- 挂载 CORSMiddleware（允许前端跨域）
- 提供 POST /chat 接口，内部同步调用 app_graph.invoke
- 监听 127.0.0.1:8000
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from schemas import ChatRequest, ChatResponse
from workflow import app_graph

app = FastAPI(title="LangGraph Sentiment Service", version="1.0.0")

# 允许前端（本地 file:// 或任意端口）跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 本地开发场景；生产环境应收窄为具体来源
    allow_credentials=False,  # allow_origins=["*"] 时不能开启 credentials
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """
    情感陪伴主接口（同步调用，不使用 async/await）。

    流程：app_graph.invoke → 识别节点 → 条件路由 → 生成节点 → 返回结果
    """
    try:
        result = app_graph.invoke({"user_input": request.user_input})
    except Exception as exc:  # 模型调用失败等上游异常，转成明确的中文错误返回
        raise HTTPException(status_code=502, detail=f"调用大模型失败：{exc}") from exc

    return ChatResponse(sentiment=result["sentiment"], response=result["response"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000)
