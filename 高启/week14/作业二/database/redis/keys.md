# Redis 文档与资料库数据

| Key | 内容 | 更新规则 |
|---|---|---|
| `rag:document:{document_id}` | `DocumentRecord` JSON，含状态、标题、摘要和解析时间 | 上传/处理中用 `SET`；完成时与概览一同事务更新 |
| `rag:corpus:profile` | `CorpusProfile` JSON，含摘要文档数和汇总文本 | 由 completed 文档摘要重建；Redis 完成事务一同提交 |
| `rag:chat:{chat_id}` | `ChatStatusRecord` JSON，含状态、阶段、时间、最终响应或安全错误摘要 | 每次阶段变化覆盖并刷新 TTL；默认 7 天，由 `RAG_CHAT_STATUS_TTL_SECONDS` 配置 |

完成记录通过 `rag:document:*` 扫描并过滤 `status=completed` 汇总；失败和处理中记录不会进入资料库概览。全量扫描让概览可由文档状态重建，成本随文档数线性增长；规模变大时可增加 completed 索引。单后端进程共享一个 `RedisDocumentStore`，完成更新在进程内加锁并使用 Redis `MULTI/EXEC` 一起写状态与概览。

问答正文和图像只留在进程内队列；`ChatStatusRecord` 不包含请求内容。问答完成、等待确认、取消或失败后的状态均按配置 TTL 保留，方便前端轮询和查看结果。
