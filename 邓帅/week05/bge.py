from sentence_transformers import SentenceTransformer
import torch

import os
print(os.getcwd())

model = SentenceTransformer("./model/bge-small-zh-v1.5/")

sentences = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错",
    "我今天心情很差",
    "我今天非常高兴"
]

query_sentences = [
    "我今天很开心"
]

embeddings = model.encode(sentences)
query_embeddings = model.encode(query_sentences)

similarities = model.similarity(query_embeddings, embeddings)[0]
scores, indices = torch.topk(similarities, k = 3)

for score, idx in zip(scores, indices):
    print(f"Score: {score:.4f}", sentences[idx])

