class AppError(Exception):
    """可安全返回给客户端的错误，不含堆栈与内部路径。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def unauthorized_error() -> AppError:
    return AppError(401, "UNAUTHORIZED", "访问令牌无效。")


def empty_input_error() -> AppError:
    return AppError(422, "EMPTY_INPUT", "输入不能为空。")


def input_too_long_error(max_length: int) -> AppError:
    return AppError(422, "INPUT_TOO_LONG", f"输入超过 {max_length} 个字符。")


def conversation_not_found_error() -> AppError:
    return AppError(404, "CONVERSATION_NOT_FOUND", "会话不存在。")


def invalid_cursor_error() -> AppError:
    return AppError(422, "INVALID_CURSOR", "分页游标无效。")


def llm_timeout_error() -> AppError:
    return AppError(503, "LLM_TIMEOUT", "模型响应超时，请稍后重试。")


def llm_rate_limited_error() -> AppError:
    return AppError(503, "LLM_RATE_LIMITED", "模型服务繁忙，请稍后重试。")


def llm_unavailable_error() -> AppError:
    return AppError(503, "LLM_UNAVAILABLE", "模型服务暂不可用，请稍后重试。")


def internal_error() -> AppError:
    return AppError(500, "INTERNAL_ERROR", "服务内部错误。")


if __name__ == "__main__":
    error = input_too_long_error(4000)
    print(error.status_code, error.code, error.message)
