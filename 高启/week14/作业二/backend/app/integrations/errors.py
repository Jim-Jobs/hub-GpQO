"""将 Redis 和 Kafka 适配器错误转换为应用可处理的类型。"""


class DocumentStoreError(RuntimeError):
    """文档状态读写失败。"""


class IngestPublishError(RuntimeError):
    """Kafka 文档任务发布失败。"""


class EmbeddingError(RuntimeError):
    """Embedding 服务不可用或返回数据不符合向量契约。"""


class VectorStoreError(RuntimeError):
    """Milvus collection 初始化或向量写入失败。"""


class SummaryError(RuntimeError):
    """文档摘要模型调用失败或返回格式无效。"""


class ChatModelError(RuntimeError):
    """Qwen3-VL 调用失败、JSON 无效或回答缺少可验证来源。"""


class ChatStatusStoreError(RuntimeError):
    """Redis 问答状态读写或格式恢复失败。"""
