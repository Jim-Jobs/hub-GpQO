# -*- coding: utf-8 -*-
"""
作业2：MCP Server（基于 2-兼容OpenAI-Agent 的 server.py 扩展）

在原有 roll_dice / get_news 两个工具的基础上，新增 3 个 MCP 工具：
    1. word_count          —— 统计文本字数（总字符 / 中文字符 / 英文单词）
    2. calculator          —— 安全计算四则运算表达式
    3. generate_password   —— 生成指定长度的随机密码

每个工具都包含：工具名、传入参数类型、返回值类型、函数注释（工具说明），
这是大模型进行"工具选择"和"参数生成"的依据（注释推荐用英文）。
"""

import ast
import operator
import random
import re
import string
from typing import Union

from fastmcp import FastMCP

mcp = FastMCP(name="Tool Kit Server")


# ================= 原有工具 =================
@mcp.tool
def roll_dice(n_dice: int) -> list[int]:
    """Roll `n_dice` 6-sided dice and return the results."""
    return [random.randint(10, 60) for _ in range(n_dice)]


@mcp.tool
def get_news() -> Union[int, str]:
    """Get latest news."""
    return "最新消息 Qwen4 is coming!"


# ================= 新增工具 1：字数统计 =================
@mcp.tool
def word_count(text: str) -> dict:
    """Count the total characters, Chinese characters and English words in the given text."""
    chinese_chars = len(re.findall(r"[一-鿿]", text))
    english_words = len(re.findall(r"[A-Za-z]+", text))
    return {
        "total_chars": len(text),
        "chinese_chars": chinese_chars,
        "english_words": english_words,
    }


# ================= 新增工具 2：安全计算器 =================
# 只允许加减乘除、幂、取模、正负号，避免 eval 带来的安全风险
_ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(node):
    """递归求值 AST 节点，只处理白名单内的运算"""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPERATORS:
        return _ALLOWED_OPERATORS[type(node.op)](
            _safe_eval(node.left), _safe_eval(node.right)
        )
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPERATORS:
        return _ALLOWED_OPERATORS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("Unsupported expression")


@mcp.tool
def calculator(expression: str) -> Union[int, float, str]:
    """Safely evaluate a simple arithmetic expression (e.g. '(3 + 5) * 2') and return the result."""
    try:
        return _safe_eval(ast.parse(expression, mode="eval"))
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


# ================= 新增工具 3：随机密码生成 =================
@mcp.tool
def generate_password(length: int) -> str:
    """Generate a random password of the given length (letters + digits + punctuation)."""
    length = max(4, length)  # 密码至少 4 位
    chars = string.ascii_letters + string.digits + string.punctuation
    return "".join(random.choices(chars, k=length))


if __name__ == "__main__":
    mcp.run(transport="sse", port=8000)
