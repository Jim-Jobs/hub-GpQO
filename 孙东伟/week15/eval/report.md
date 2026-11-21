# 双引擎对比实验

- 生成时间：2026-11-20 00:14
- 运行模式：`mock`（`mock` 为离线规则引擎，不代表真实模型能力）
- 模型：`gpt-4o-mini`

## 汇总

| 引擎 | 标签准确率 | 平均耗时 | 危机命中 |
| --- | --- | --- | --- |
| agents | 92% | 0 ms | 1 |
| langgraph | 92% | 2 ms | 1 |

## 逐条结果

| # | 输入 | 期望 | 引擎 | 实际 | 置信度 | 耗时 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 今天答辩通过了，导师还夸我思路清楚，太开心了！ | positive | langgraph | positive | 0.76 | 7 ms |
| 1 | 今天答辩通过了，导师还夸我思路清楚，太开心了！ | positive | agents | positive | 0.76 | 0 ms |
| 2 | 拿到心仪的 offer 了，谢谢这段时间帮我改简历的朋友。 | positive | langgraph | positive | 0.83 | 1 ms |
| 2 | 拿到心仪的 offer 了，谢谢这段时间帮我改简历的朋友。 | positive | agents | positive | 0.83 | 0 ms |
| 3 | 连着加班两周，项目还是被否了，感觉自己挺没用的。 | negative | langgraph | negative | 0.83 | 1 ms |
| 3 | 连着加班两周，项目还是被否了，感觉自己挺没用的。 | negative | agents | negative | 0.83 | 0 ms |
| 4 | 最近失眠特别严重，一躺下就开始焦虑。 | negative | langgraph | negative | 0.76 | 1 ms |
| 4 | 最近失眠特别严重，一躺下就开始焦虑。 | negative | agents | negative | 0.76 | 0 ms |
| 5 | 今天不开心，什么都不想干。 | negative | langgraph | negative | 0.69 | 1 ms |
| 5 | 今天不开心，什么都不想干。 | negative | agents | negative | 0.69 | 0 ms |
| 6 | 明天要去体检，有点紧张。 | neutral | langgraph | neutral | 0.62 | 1 ms |
| 6 | 明天要去体检，有点紧张。 | neutral | agents | neutral | 0.62 | 0 ms |
| 7 | 随便聊聊，最近天气转凉了。 | neutral | langgraph | neutral | 0.62 | 1 ms |
| 7 | 随便聊聊，最近天气转凉了。 | neutral | agents | neutral | 0.62 | 0 ms |
| 8 | 呵呵，真是谢谢领导给我加了这么多活。 | negative | langgraph | positive | 0.69 | 1 ms |
| 8 | 呵呵，真是谢谢领导给我加了这么多活。 | negative | agents | positive | 0.69 | 0 ms |
| 9 | 说不清什么感觉，有点累，但也还行。 | neutral | langgraph | neutral | 0.62 | 1 ms |
| 9 | 说不清什么感觉，有点累，但也还行。 | neutral | agents | neutral | 0.62 | 0 ms |
| 10 | 我最近真的撑不住了，不想活了。 | negative | langgraph | negative | 0.99 | 1 ms |
| 10 | 我最近真的撑不住了，不想活了。 | negative | agents | negative | 0.99 | 0 ms |
| 11 | 这次考试又没过，我是不是真的很差劲。 | negative | langgraph | negative | 0.69 | 1 ms |
| 11 | 这次考试又没过，我是不是真的很差劲。 | negative | agents | negative | 0.69 | 0 ms |
| 12 | 今天面试发挥得不错，希望能有好结果。 | positive | langgraph | positive | 0.69 | 1 ms |
| 12 | 今天面试发挥得不错，希望能有好结果。 | positive | agents | positive | 0.69 | 0 ms |
