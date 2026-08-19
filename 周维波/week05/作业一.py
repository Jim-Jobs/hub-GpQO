"""
使用 BGE 模型进行文本检索（无需 ES）
模型: BAAI/bge-small-zh-v1.5
"""
import os
# 强制离线模式（模型已缓存到本地，避免联网重试）
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from sentence_transformers import SentenceTransformer
import numpy as np

# 1. 加载 BGE 中文模型（首次运行会自动下载，约 100MB）
model = SentenceTransformer("BAAI/bge-small-zh-v1.5")

# 2. 数据库文本
corpus = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错",
]

# 3. 查询文本
query = "我今天很开心"

# 4. 编码（BGE 模型对 query 建议加前缀，提升检索效果）
query_embedding = model.encode(
    [query],
    normalize_embeddings=True,
    prompt="为这个句子生成表示以用于检索相关文章："  # BGE 中文 query 前缀
)
corpus_embeddings = model.encode(corpus, normalize_embeddings=True)

# 5. 计算余弦相似度（已归一化，点积即余弦相似度）
scores = np.dot(query_embedding, corpus_embeddings.T)[0]

# 6. 按相似度降序排列
ranked = sorted(zip(corpus, scores), key=lambda x: x[1], reverse=True)

# 7. 输出结果
print(f"查询文本: {query}\n")
print("检索结果（按相似度降序）:")
print("-" * 50)
for i, (text, score) in enumerate(ranked, 1):
    print(f"  排名 {i}  相似度: {score:.4f}  文本: {text}")
print("-" * 50)
