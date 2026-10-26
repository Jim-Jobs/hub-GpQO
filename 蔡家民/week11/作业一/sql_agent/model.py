from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if not lines[-1].strip() == "```":
            raise ValueError("Unclosed JSON code block")
        text = "\n".join(lines[1:-1])
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("Model output must be a JSON object")
    return obj


class OpenAICompatibleModel:
    """Standard-library HTTP client. Never uses keys from the course source files."""

    mode = "live"

    def __init__(self, config_path: Path):
        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        self.base_url = os.getenv(self.config["base_url_env"], "").rstrip("/")
        self.api_key = os.getenv(self.config["api_key_env"], "")
        self.model = os.getenv(self.config["model_env"], "")
        if not all((self.base_url, self.api_key, self.model)):
            raise ValueError("Set SQL_AGENT_BASE_URL, SQL_AGENT_API_KEY and SQL_AGENT_MODEL before live use")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}):
            raise ValueError("Model endpoint must use HTTPS (HTTP allowed only on localhost)")
        self.calls: list[dict[str, Any]] = []

    def complete(self, stage: str, system: str, context: dict) -> dict:
        payload = {"model": self.model, "temperature": self.config.get("temperature", 0),
                   "max_tokens": self.config.get("max_output_tokens", 3000),
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]}
        if self.config.get("json_mode"):
            payload["response_format"] = {"type": "json_object"}
        request = urllib.request.Request(self.base_url + "/chat/completions",
                                         data=json.dumps(payload).encode("utf-8"),
                                         headers={"Authorization": "Bearer " + self.api_key,
                                                  "Content-Type": "application/json"}, method="POST")
        started = time.monotonic()
        log = {"stage": stage, "model": self.model, "usage": None, "status": "request_started"}
        self.calls.append(log)
        # Transport retries are intentionally absent to avoid silent extra costs.
        try:
            with urllib.request.urlopen(request, timeout=self.config.get("request_timeout_seconds", 60)) as response:
                raw = json.load(response)
        except urllib.error.HTTPError as exc:
            log.update(status="http_error", http_status=exc.code, elapsed_ms=(time.monotonic() - started) * 1000)
            raise RuntimeError(f"Model HTTP {exc.code}; check model/base URL/account configuration") from None
        except urllib.error.URLError:
            log.update(status="connection_error", elapsed_ms=(time.monotonic() - started) * 1000)
            raise RuntimeError("Model connection failed; check endpoint/network") from None
        usage = raw.get("usage")
        log.update(usage=usage, status="response_received", elapsed_ms=(time.monotonic() - started) * 1000)
        content = raw["choices"][0]["message"]["content"]
        return parse_json(content)


class ReplayModel:
    """Explicit fixtures exercise orchestration. These are NOT model capability scores."""

    mode = "replay"
    model = "scripted-fixture"

    def __init__(self, fixture: dict):
        self.fixture = fixture
        self.calls = []
        self.candidate_index = 0

    def complete(self, stage: str, system: str, context: dict) -> dict:
        self.calls.append({"stage": stage, "model": self.model, "usage": None})
        if stage in {"sql_generation", "sql_rewrite"}:
            candidates = self.fixture["candidates"]
            idx = self.candidate_index
            self.candidate_index += 1
            if idx >= len(candidates):
                raise ValueError("Replay fixture has no next SQL candidate")
            return candidates[idx]
        if stage == "summary":
            execution = context["execution"]
            # Deterministic evidence rendering for replay, no invented natural-language quality score.
            rows = execution["rows"]
            return {"text": "离线回放：查询返回 " + str(len(rows)) + " 行。\n" +
                            json.dumps({"columns": execution["columns"], "rows": rows}, ensure_ascii=False),
                    "facts": [], "limitations": ["仅验证工作流，未调用大模型。"]}
        return self.fixture[stage]
