"""
1. 本地安装下 sentence-transformer库，使用bge模型进行文本检索，不需要es

```
modelscope download --model BAAI/bge-small-zh-v1.5  --local_dir BAAI/bge-small-zh-v1.5
```

待检索的文本：我今天很开心

数据库文本：

- 我喜欢机器学习
- 我喜欢深度学习
- 我今天心情很不错
"""

from sentence_transformers import SentenceTransformer
import torch

# 1. 加载模型
# bge-small-zh-v1.5 对中文语义匹配效果较好
model_path = "../models/BAAI/bge-small-zh-v1.5/"
model = SentenceTransformer(model_path)

# 2. 准备数据
query_text = "我今天很开心"
corpus = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错"
]

# 3. 编码向量 (关键：开启归一化)
# normalize_embeddings=True: 确保余弦相似度计算准确，且兼容点积加速
query_embedding = model.encode(query_text, convert_to_tensor=True, normalize_embeddings=True)
corpus_embeddings = model.encode(corpus, convert_to_tensor=True, normalize_embeddings=True)

# 4. 计算相似度
# model.similarity 内部执行矩阵乘法 (Dot Product)，因已归一化，等价于余弦相似度
similarities = model.similarity(query_embedding, corpus_embeddings)

# 5. 获取结果并排序
# similarities 形状为 (1, 3)，squeeze(0) 变为 (3,)
scores = similarities.squeeze(0)
indices = torch.argsort(scores, descending=True) # 按分数降序排列

# 6. 输出检索结果
print(f"查询语句: '{query_text}'\n" + "-"*30)
for idx in indices:
    score = scores[idx].item()
    text = corpus[idx]
    print(f"[Score: {score:.4f}] {text}")
