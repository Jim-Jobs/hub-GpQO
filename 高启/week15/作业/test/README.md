# 共享测试数据与评测

`cases.jsonl` 保存两种实现共用的 30 条中文情感识别样例；正向、负向、中性各 10 条。`case_id` 是样例稳定编号，`text` 是输入，`expected_sentiment` 是人工标注标签。测试集使用合成文本，不用于真实用户数据。

`reply_scores.csv` 是人工评分表模板。回复评价采用人工打分，评分维度、量表和通过阈值尚待确认，因此模板暂不预填分值。

批量运行前，先启动目标项目的 Redis、API、情感识别 worker 和回复 worker，并确认 `.env` 已配置 DeepSeek。然后在 `project--04` 根目录用 `run_cases` 将 30 条样例逐条提交到 API。每条样例会经过完整情感识别和回复流程，预计产生两次模型调用；运行会消耗 API 额度。

```powershell
python -m test.run_cases --api-url http://localhost:8000 --framework langgraph --results .\test\results\langgraph\run.jsonl
python -m test.run_cases --api-url http://localhost:8001 --framework openai-agents --results .\test\results\openai-agents\run.jsonl
```

结果 JSONL 会自动保存到 `test/results/<framework>/`，命令结束时会打印实际文件路径。使用该路径汇总指标：

```powershell
python -m test.evaluate --results .\test\results\langgraph\run.jsonl
python -m test.evaluate --results .\test\results\openai-agents\run.jsonl
```

评测器输出 Accuracy、Macro-F1、每类 precision/recall/F1、混淆矩阵、格式有效率、人工评分均值、延迟均值/p50/p95 和失败数。当前 API 不返回 Token 使用量，因此 Token 字段留空；评测器只汇总 JSONL，不调用 Agent。

共享评测器的单元测试在 `project--04` 根目录运行：

```powershell
python -m unittest discover -s test -p "test_*.py" -v
```
