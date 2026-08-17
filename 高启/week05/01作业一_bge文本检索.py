from sentence_transformers import SentenceTransformer
import numpy as np

model = SentenceTransformer("../models/bge-small-zh-v1.5/") # 没有暴露tokenizer、 model

db_documents = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错"
]

db_embeddings = model.encode(db_documents) # 正向传播 -》 句子编码 （token的编码 -》 mean pooling）
print(db_embeddings.shape)

def search(query,top_k=3):
    query_embedding = model.encode([query], normalize_embeddings=True)
    similarities = np.dot(db_embeddings, query_embedding.T).flatten()
    top_indices = np.argsort(similarities)[::-1][:top_k]
    print(f"\n 检索输入: '{query}'")
    print("-" * 40)
    for i, idx in enumerate(top_indices):
        score = similarities[idx]
        print(f"Rank {i + 1} [相似度: {score:.4f}]: {db_documents[idx]}")


search("我今天很开心")



