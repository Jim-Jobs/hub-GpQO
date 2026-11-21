"""测试统一在离线模式下跑：不联网、不消耗 token、结果确定。"""

import os

os.environ.setdefault("APP_MODE", "mock")
os.environ.pop("LLM_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)
