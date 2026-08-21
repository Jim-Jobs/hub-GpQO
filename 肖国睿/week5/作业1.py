从 sentence_transformers 导入 SentenceTransformer, util
导入 torch

# 模型相对路径
模型 = SentenceTransformer("./BAAI/bge-small-zh-v1.5")



# 数据库文本：候选知识库
语料库 = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错"
]

# 查询待检索文本
查询 = "我今天很开心"


查询前缀 = "为这个句子生成表示以用于检索："
corpus_prefix = "为这个句子生成表示以用于检索："

query_text = query_prefix + query
corpus_texts = [corpus_prefix + text for text in corpus]



# 知识库全部编码成向量
corpus_embeddings = model.encode(corpus_texts, convert_to_tensor=True)
# 查询语句编码向量
query_embedding = model.encode(query_text, convert_to_tensor=True)


# util.cos_sim 计算余弦相似度，值越接近1代表句子语义越接近
cos_scores = util.cos_sim(query_embedding, corpus_embeddings)[0]

# 把分数+原文打包，按相似度从高到低排序
results = []
对于 idx, score 在 enumerate(cos_scores):
    结果。追加({
        "text": corpus[idx],
        "score": float(score)
    })

# 按相似度降序排序
results = sorted(results, key=lambda x: x["score"], reverse=True)

# 输出
print(f"查询语句：{query}\n")
对于项目 在结果中：
    print(f"文本：{item['text']} ｜相似度分数：{item['score']:.4f}")
