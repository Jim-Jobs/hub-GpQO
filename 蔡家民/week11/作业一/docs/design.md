# 架构与设计

## 数据流和职责

数据库说明书和业务规则属于稳定上下文；用户问题、确认意图、候选表、SQL、校验报告和执行结果属于一次运行状态。Workflow显式组织节点，不依赖模型自行无限调用工具。

模型节点为意图、选表、生成、语义审查、改写、汇总。解析、外键图、SQLite授权、执行、重试预算、状态存储和结果比较由确定性程序负责。

## 节点契约

| 对象 | 主要字段 |
|---|---|
| QueryIntent | status、metric、dimensions、filters、time_range、grain、assumptions |
| TableSelection | tables、columns、joins、bridge_tables、reason |
| SQLCandidate | sql、params、explanation、changes |
| ValidationReport | passed、checks；每项带错误类型、证据和可重试标记 |
| ExecutionResult | status、columns、rows、truncated、elapsed_ms、error_type |
| FinalAnswer | text、facts、limitations |
| WorkflowResult | run_id、status、mode、各节点结果、所有候选与trace |

阶段状态success、needs_clarification、unsupported、failed分开表示。SQL结果为空并非失败，结果截断并非完整答案。

## SQL校验的边界

先限定查询入口，再使用SQLite自己的编译与authorizer验证嵌套操作、表访问和函数。正则只用于有限业务诊断，不构成权限边界。授权回调考虑COUNT(*)可能不提供database名称的情况。

已支持语义风险：未声明表访问、直接JOIN明细后SUM订单头Total、已确认日期边界缺失。日期字符串出现只能构成弱证据，不能证明WHERE逻辑正确；复杂SQL依赖后续模型审查与结果评测。

模型审查意见是可错的诊断，不是通用证明。为了让校验器更好，应从验证集错误归纳新增可测规则，而不是无限增加含糊提示词。

## 修复和终止

最多三个SQL候选；每次改写重新编译、授权和语义检查。重复SQL与参数提前终止，禁止操作不自动重试。选表范围不匹配时触发重新选表再改写。语法/字段错误直接修复；资源限制允许受预算约束的改写。

当前没有将所有no-such-table错误自动定位到选表，也不具备通用重新规划引擎。完整失败保存在trace，便于人工复盘。

## 可观察性

每次运行生成独立JSON，包含节点输出、耗时、提示词版本、数据库指纹、启用开关、模型实际返回usage。没有usage就留空，不伪造token或人民币价格。

最早失败节点表示检测位置，不证明根因。已通过stages命令实现标准上游替换和独立节点评测，恢复情况是诊断证据而非唯一根因证明。

## 运行模式

live只接受环境明确配置的服务；replay只使用题集fixture，在所有结果与报告标记。回放内的SQL仍在真实数据库执行，但表选择/SQL候选来自事先提供的脚本，不能证明模型选择和生成能力。

## 后续重构条件

节点和契约稳定后才考虑图框架；多应用复用工具后再考虑MCP；需要展示交互轨迹时再做前端。目前标准库CLI与Notebook足够验证核心作业。
