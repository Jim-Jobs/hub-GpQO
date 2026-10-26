# Chinook SQL Agent Workflow

作业一：在Chinook音乐商店数据库上实现可观察、可校验、可改写的七阶段SQL问数流程。

## 已实现

- 数据库解析：11张业务表、字段、外键、索引、统计、受限样例及指纹缓存。
- 意图确认与选表：模型结构化输出，歧义澄清，不支持问题退出，外键桥接补全。
- SQL生成：SQLite方言、命名参数、确认口径。
- SQL校验：查询结构入口、SQLite编译和授权、选表范围一致性，以及有限的业务风险规则；真实模式另有模型语义审查。
- SQL改写：错误证据回填，最多2次改写，重复候选终止，选表范围不一致时重新选择。
- SQL执行：只读连接、操作与函数许可、计算预算、行数上限、截断标记。
- 结果汇总：证据输入、空结果与截断限制说明、完整JSON trace。
- 120道用例、79个查询模板族、参考SQL快照、分阶段记录、端到端结果比较及可选独立模型评分接口。
- 30项独立校验/修复专项、3组反例检查、标准上游输出替换诊断、重复实验脚本。
- 基线/完整流程、禁用改写、禁用业务规则等实验开关。

**离线回放只验证程序流程，不代表大模型能力。** 当前replay报告没有调用真实模型，不可作为模型准确率提交。120题已完成作者侧语义审阅、参数模板分组和执行核验；11项关键统计另用Python独立计算交叉检查。这不等同于独立第三方人工认证，也不证明SQL在所有数据库上等价。真实模型成绩与裁判校准需要配置服务后实际运行。

不依赖模型服务的开发与检查均已完成。你只需按docs/handoff.md配置自己的服务；不需要重新编写代码、安装第三方依赖或手工造数据。

## 环境

Python 3.11或更高版本。核心程序、HTTP模型适配器与测试只使用标准库，可直接运行，不需要先pip安装。

从当前目录运行：

```powershell
python -m unittest discover -s tests -v
python -m sql_agent check-dataset
python -m sql_agent evaluate --mode replay --split all --report replay_smoke
# 完整验收，包括专项、反例、分阶段和重复回放
python scripts/verify.py
```

如果python指向不合适的环境，换成自己Python解释器的完整路径。不要复制课程文件中的密钥。

## 三个快速入口

### 1. 查看数据库说明书

```powershell
python -m sql_agent catalog
```

生成的缓存位于artifacts/catalog。数据库指纹改变会使用新缓存。日期范围为2009—2013年，不代表当前年份。

### 2. 离线运行一个已知用例

```powershell
python -m sql_agent ask "客户总人数是多少？" --replay-case S01 --reference-date 2014-01-15
```

回放输入必须与用例问题一致。模型各节点的输出来自显式fixture，最终SQL仍会在本地只读数据库执行。

演示错误字段自动修复：

```powershell
python -m sql_agent ask "客户总人数是多少？" --fixture examples/repair_fixture.json --reference-date 2014-01-15
```

这一演示人为注入MissingColumn类错误，不能宣称是模型自然产生并修复的错误。trace会保留第一稿编译错误、第二稿、复验与执行结果。

### 3. 使用自己的真实模型

采用兼容Chat Completions的HTTP接口；模型是否支持此接口及参数需按供应商实际服务确认。base URL应包含版本前缀，但不要包含/chat/completions。

在PowerShell可运行交互配置脚本，API Key隐藏输入且不写入文件：

```powershell
.\scripts\configure_model.ps1
```

也可以在当前终端手动设置：

```powershell
$env:SQL_AGENT_BASE_URL = "https://你的模型服务地址/v1"
$env:SQL_AGENT_MODEL = "你的模型名称"
# 用隐藏输入，避免把密钥写进终端命令历史
$credential = Get-Credential -UserName "api" -Message "将API Key填在密码栏"
$env:SQL_AGENT_API_KEY = $credential.GetNetworkCredential().Password
python -m sql_agent ask "2013年每个有订单月份的销售额是多少？" --reference-date 2014-01-15
```

没有.env自动加载逻辑；环境变量需要在运行Python的同一个终端设置。系统只读取上述配置，不扫描其他项目密钥。真实模式会产生供应商API用量。

固定JSON输出为提示词要求，config/model_example.json中的json_mode可按供应商能力启用。当前temperature、max_tokens和JSON兼容性仍需在所选服务做真实联调。

## 工作流

```text
数据库解析/业务规则 → 问题+reference_date+已确认澄清
                         ↓
                     意图与口径
              ┌──────────┼──────────┐
           澄清         可回答       不支持
                        ↓
                     选表/桥接
                        ↓
                      生成SQL
                        ↓
                      分层校验 ←──改写SQL
                   通过 ↓      失败 ↑
                      执行SQL ──────┘（可修复错误）
                        ↓
                      结果汇总
```

澄清是显式返回状态。收到用户回答后再次运行原问题并加`--clarification "按账单国家统计"`。当前不是自动持久化的聊天会话系统。

## 项目结构

```text
sql_agent/
  contracts.py       节点与状态契约
  catalog.py         数据库说明书与外键桥接
  model.py           真实HTTP客户端/显式回放客户端
  prompts.py         分节点提示词及版本
  validator.py       编译授权与有限业务风险校验
  executor.py        受限SQLite执行器
  workflow.py        节点编排、修复、终止和trace
  evaluation.py      结果比对、分阶段记录、评测报告
  cli.py             命令行
config/              业务口径和模型配置样例
data/chinook.db      课程数据库副本
evaluation/          种子题集、元信息与回放fixture
examples/            自动修复的可复现示例
notebooks/demo.ipynb 演示入口
tests/               单元/集成测试
docs/                设计、评测和当前局限
reports/             运行报告
artifacts/           元数据缓存与运行trace
```

## 评测与实验

```powershell
# 真实模式先用开发集3题估算用量
python -m sql_agent evaluate --mode live --split dev --limit 3 --report live_pilot
# 同一验证集比较基线与工作流
python -m sql_agent evaluate --mode live --split validation --variant baseline --report baseline_validation
python -m sql_agent evaluate --mode live --split validation --report workflow_validation
# 改写和口径消融
python -m sql_agent evaluate --mode live --split validation --max-rewrites 0 --report no_rewrite_validation
python -m sql_agent evaluate --mode live --split validation --no-rules --report no_rules_validation
```

`--no-rules`只关闭显式业务规则上下文，不会移除所有提示词中的领域知识或校验规则，因此它是“业务规则上下文消融”，不是完全移除语义知识的严格消融。正式研究需进一步控制这些因素。

报告包含结果匹配、首稿可执行、必要表召回、冗余表数、改写次数、最早检测失败节点、耗时、模型用量。列位置必须与参考答案语义一致；当前不做任意列重排等价匹配。金额容差、顺序策略、重复行和NULL分别处理。金额排序按两位小数比较并列，再使用题目指定次级排序，避免浮点累加误差改变次序。

### 数据与专项核验

```powershell
python -m sql_agent audit
python -m sql_agent diagnostics
```

### 独立环节与错误定位

```powershell
# 正确上游输入下分别评测选表、SQL生成和回答汇总
python -m sql_agent stages --mode live --split validation --limit 3
# 对指定题逐步替换意图、选表、SQL，辅助定位错误
python -m sql_agent stages --mode live --diagnose-case S11
```

这些是专门的诊断协议，不能与普通端到端成绩混合。恢复查询结果也不能单独证明回答质量或唯一根因。

### 可复现的比较与重复实验

```powershell
# 默认只打印计划，不发送请求
python scripts/run_experiments.py --mode live --split dev --limit 3
# 显式执行后比较同一批题的基线和完整工作流，共享请求上限60次
python scripts/run_experiments.py --mode live --split dev --limit 3 --execute
# 稳定性与消融按指定重复次数运行；先确认小样本用量
python scripts/run_experiments.py --mode live --split validation --limit 3 --repeats 3 --variants baseline workflow no_rewrite --max-requests 180 --execute
```

如要启用文本评分，复制model_example.json为独立裁判配置，改成读取SQL_AGENT_JUDGE_*环境变量，再传`--judge-config`。使用独立模型并人工抽样校准。回放模式禁止启用真实裁判，以免把脚本答案误记为模型表现。

## 重要局限

1. SQL业务校验目前是有限启发式+可选模型审查，不是通用语义正确性证明。订单总金额重复聚合检查有明确适用范围，复杂CTE需要评测和审查。
2. 只读授权与计算预算不是任意恶意查询的完整沙箱；没有进程级内存隔离。不要对公网开放执行器。
3. 外键最短路径只补全候选桥接关系，不能证明业务JOIN正确；已经连接的表也可能语义选错。
4. 已实现独立环节、标准输出替换和重复实验工具；目前只运行了离线协议检查，真实模型对比仍待配置服务。没有增加持久化聊天系统。
5. 参数变体按模板族分组，切分为20/40/60；尚不能声称排除了所有语义近似题，也没有独立第三方认证。
6. 当前只核对澄清/拒绝状态，未自动证明理由准确；文本质量需独立评分与人工审阅。
7. traces可能含用户问题和查询结果，不应未经检查上传公开仓库。它们不记录API Key。

## 资料来源

数据库来源于课程提供的chinook.db副本，SHA-256在数据集manifest中记录。原课程文件未修改。

- Python sqlite3：https://docs.python.org/3/library/sqlite3.html
- SQLite EXPLAIN：https://www.sqlite.org/lang_explain.html
- SQLite查询计划：https://sqlite.org/eqp.html

未提交Git、未推送远程、未运行真实付费模型评测。
