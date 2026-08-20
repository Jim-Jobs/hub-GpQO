"""
作业二：
本地安装Ollama， 本地运行 qwen3-0.6b 完成 sdk调用

运行前，需要本地启动ollama
"""

from openai import OpenAI

# --- 配置区域 ---
BASE_URL = "http://localhost:11434/v1"
API_KEY = "ollama"
MODEL_NAME = "qwen3:0.6b"


def get_qwen_sync_response(prompt: str, system_prompt: str = "你是一个有用的助手。"):
    """
    同步调用 Qwen3-0.6B 获取回复
    """
    try:
        client = OpenAI(
            base_url=BASE_URL,
            api_key=API_KEY
        )

        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=512
        )

        # 【修正点】增加索引  获取第一个选择项
        return response.choices[0].message.content

    except Exception as e:
        return f"调用出错: {str(e)}"


if __name__ == "__main__":
    question = "请用一句话介绍武汉的代表美食"
    print(f"用户: {question}")
    answer = get_qwen_sync_response(question)
    print(f"AI: {answer}")
