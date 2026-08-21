from openai import OpenAI

# 初始化客户端，指向 Ollama 的本地服务
client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="1111"  # 任意值即可
)

# 发送请求，模型名称需与 `ollama list` 显示的一致
response = client.chat.completions.create(
    model="qwen3:0.6b",          # 这里改为你实际拉取的名称
    messages=[
        {"role": "system", "content": "你是一个有帮助的助手。"},
        {"role": "user", "content": "你好"}
    ],
    temperature=0.7,
    max_tokens=512
)

print(response.choices[0].message.content)