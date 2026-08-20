from sentence_transformers import SentenceTransformer, util

# 加载本地下载好的两个模型
bert_model = SentenceTransformer("D:\\work\\forme\\study\\0 AI\\models\\bert-base-chinese")
# BAAI 目录下是 BGE 中文句向量模型（bge-small-zh）
sbert_model = SentenceTransformer("D:\\work\\forme\\study\\0 AI\\models\\BAAI")

# 待检索的文本
query = "我今天很开心"

# 数据库文本
corpus = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错",
]


def retrieval(model, model_name, query, corpus):
    """用指定模型对 query 在 corpus 中做相似度检索，按相似度从高到低输出"""
    # 编码时做归一化，这样向量点积结果就是余弦相似度
    query_vec = model.encode(query, normalize_embeddings=True)
    corpus_vecs = model.encode(corpus, normalize_embeddings=True)

    # 计算 query 与每条语料的余弦相似度
    similarities = model.similarity(query_vec, corpus_vecs)[0]

    print(f"===== {model_name} 的检索结果 =====")
    for text, score in sorted(zip(corpus, similarities.tolist()), key=lambda x: -x[1]):
        print(f"  相似度 {score:.4f}  {text}")

    best_text, best_score = max(zip(corpus, similarities.tolist()), key=lambda x: x[1])
    print(f"  最相似的是：{best_text}（相似度 {best_score:.4f}）")
    print()


# 1) 用 bert-base-chinese 检索
retrieval(bert_model, "bert-base-chinese", query, corpus)

# 2) 用 BGE 模型检索
retrieval(sbert_model, "BGE(bge-small-zh)", query, corpus)