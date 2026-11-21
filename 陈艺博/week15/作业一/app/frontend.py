import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> None:
    parser = argparse.ArgumentParser(description="情感识别与回应命令行前端")
    parser.add_argument("--framework", choices=("langgraph", "agents"), default="langgraph", help="选择后端实现")
    parser.add_argument("--server", default=f"http://{os.getenv('HOST', '127.0.0.1')}:{os.getenv('PORT', '8000')}", help="后端地址")
    args = parser.parse_args()
    endpoint = f"{args.server.rstrip('/')}/api/{args.framework}"
    print(f"情感支持前端（{args.framework}），输入 exit 退出。")
    while True:
        try:
            text = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if text.lower() in {"exit", "quit", "q"}:
            break
        if not text:
            continue
        payload = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
        request = Request(endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=120) as response:
                result = json.loads(response.read().decode("utf-8"))
            print(f"[情感：{result['emotion']}；依据：{result['reason']}]")
            print(f"助手：{result['reply']}\n")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            print(f"请求失败（HTTP {exc.code}）：{detail}", file=sys.stderr)
        except URLError as exc:
            print(f"无法连接后端：{exc.reason}", file=sys.stderr)


if __name__ == "__main__":
    main()
