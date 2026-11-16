# Milvus Lite Collection

运行时由 `backend/app/integrations/milvus_store.py` 创建 `rag_chunks`；不需要手动执行建表脚本。

| 字段 | 类型 | 用途 |
|---|---|---|
| `chunk_id` | VARCHAR(64)，主键 | SHA-256 稳定 ID，Kafka 重投时 upsert 同一记录 |
| `document_id` | VARCHAR(256) | 回到文档及 Redis 状态 |
| `page_number` | INT64 | PDF 原始页码，从 1 开始 |
| `chunk_index` | INT64 | 文档页内块序号，从 0 开始 |
| `text` | VARCHAR(8192) | 当前页文本块，默认最多 800 个字符 |
| `asset_ids` | ARRAY<VARCHAR(256)>，容量 4096 | 与本页文本关联的 MinerU 图片资源 ID |
| `embedding` | FLOAT_VECTOR(1024) | BGE-M3 dense 向量，COSINE 距离 |

collection 路径为 `RAG_DATA_ROOT/milvus/milvus_lite.db`。单进程通过 `MilvusChunkStore.from_path()` 复用客户端，同 collection upsert 经同一实例的锁串行执行，每批最多写 256 块；部分批次失败时 Kafka 重投会用稳定主键覆盖已成功批次。
