from sentence_transformers import SentenceTransformer
# 必备库，基于transformers，用途是做模型推理、sbert训练过程的用途

model = SentenceTransformer("models/google-bert--bert-base-chinese") # 没有暴露tokenizer、 model

query = "我今天很开心"
sentences = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错"
]

s_embeddings = model.encode(sentences) # 正向传播 -》 句子编码 （token的编码 -》 mean pooling）
q_embeddings = model.encode(query)

similarities = model.similarity(s_embeddings, q_embeddings)
sim_scores = similarities.numpy().flatten()  # 展平为一维数组
results = sorted(zip(sim_scores, sentences), key=lambda x: x[1])
print(f"查询文本: {query}")
print("\n检索结果：")
for score, text in results:
    print(f"相似度: {score:.4f} - {text}")



