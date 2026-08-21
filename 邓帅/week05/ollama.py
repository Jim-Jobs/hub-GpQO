from langchain_openai import ChatOpenAI
from langchain.messages import SystemMessage, HumanMessage
from pydantic import SecretStr


model = ChatOpenAI(
    model="qwen3:0.6b",
    base_url="http://localhost:11434/v1",
    api_key=SecretStr("sk-111"),
    temperature=0.7,
    max_completion_tokens=512
)

msg = [
    SystemMessage("你是一个有帮助的助手"),
    HumanMessage("你好，你是谁")
]

response = model.invoke(msg)

print(response.content)
