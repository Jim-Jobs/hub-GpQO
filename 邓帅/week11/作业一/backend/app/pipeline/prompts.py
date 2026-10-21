"""pipeline 各步的 prompt 模板(参考课件 demo 的 answer_prompt/answer_rewrite_prompt 风格增强)。"""

# 第 2 步:table 选择
SELECT_TABLES_PROMPT = '''你是一个专业的数据库专家。数据库中有多张数据表,现在需要你根据用户的提问,判断回答该问题需要查询哪些表。

候选表清单(表名: 字段列表):
{tables_brief}

各表之间的外键关系:
{relations}

用户提问:{question}

请直接输出需要查询的表名,以 JSON 数组格式输出,不要输出任何其他内容。例如: ["customers", "invoices"]
只从候选表清单中选择,不要编造不存在的表名。'''

# 第 3 步:SQL 生成
GENERATE_SQL_PROMPT = '''你是一个专业的数据库专家,现在需要你根据用户提问生成对应的 SQLite 查询语句。请直接输出 SQL,不需要有任何解释或额外输出。

用户提问:{question}

可以使用的表及其字段(字段名: 类型, 取值样例):
{tables_schema}

各表样例数据:
{tables_sample}

相关的外键关系(用于表连接):
{relations}

要求:
1. 只能使用上面给出的表和字段,只能生成 SELECT 查询;
2. 需要跨表信息时使用 JOIN,连接条件参考外键关系;
3. 聚合统计注意使用 GROUP BY,输出列使用有意义的别名(用英文);
4. 生成的 SQL 必须是单条语句。'''

# 第 5 步:SQL 改写
REWRITE_SQL_PROMPT = '''你是一个专业的数据库专家。此前为用户提问生成的 SQL 没有通过校验,现在需要你根据错误信息修正这条 SQL。请直接输出修正后的 SQL,不要输出任何解释。

用户提问:{question}

此前使用的表:
{tables}

原 SQL:
{sql}

校验发现的错误:
{errors}

要求:修正错误,只输出一条 SELECT 语句。'''

# 第 7 步:结果汇总
SUMMARIZE_PROMPT = '''你是一个专业的数据库专家,请将下面 SQL 查询的结果组织为自然语言回答,回复用户的提问。要求:
1. 直接回答提问,给出具体数值和结论;
2. 结果为空时如实说明没有查到数据;
3. 不要编造结果中不存在的数据。

用户提问:{question}

执行 SQL:{sql}

查询结果(前 {row_limit} 行):
{result}
'''
