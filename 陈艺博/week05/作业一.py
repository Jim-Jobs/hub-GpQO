from sentence_transformers import SentenceTransformer
import torch

# 加载本地模型（指向下载目录）
model = SentenceTransformer("E:/八斗学院/models/BAAI/bge-small-zh-v1.5/")

# 待检索的查询
query = "我今天很开心"

# 候选文档列表
documents = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错"
]

# 编码查询和文档（转为向量）
query_emb = model.encode(query, normalize_embeddings=True)   # 归一化便于余弦相似度
doc_embs = model.encode(documents, normalize_embeddings=True)

# 转为 torch 张量（sentence-transformers v5 返回 numpy 数组）
query_emb = torch.from_numpy(query_emb)
doc_embs = torch.from_numpy(doc_embs)

# 计算相似度（点积即余弦相似度，因为已归一化）
scores = torch.matmul(query_emb, doc_embs.T)

# 排序并输出最相关文档
top_idx = torch.argmax(scores).item()
print(f"查询：{query}")
print(f"最相关文档：{documents[top_idx]}，相似度得分：{scores[top_idx]:.4f}")

# 如果需要全部排序结果：
sorted_indices = torch.argsort(scores, descending=True)
print("\n所有文档按相似度降序：")
for idx in sorted_indices:
    print(f"{documents[idx]}：{scores[idx]:.4f}")