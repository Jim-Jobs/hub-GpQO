# AGENTS.md

面向在此仓库工作的 AI 编码代理。人类读者请从 [README](./README.md) 开始；本文件只约定协作方式与质量门槛，不重复设计细节。

## 项目现状

- 引擎与测试已落地：`scripts/`（检索 / 打分 / 总结 / 渲染 / 编排 / 契约）+ `tests/`（契约测试全绿，数量见 README §0）。
- CI 骨架已就位：`.github/workflows/ci.yml`（ruff lint + mypy 类型检查 + 离线单测 + pip-audit 依赖扫描）。
- 端到端生成报告需在 `config/.env` 填入真实搜索 / LLM Key；无 Key 时 `run.py` 以退出码 2 快速失败。
- 状态与路线图以 README §0、§16 为准；变更状态时同步更新。

## 职责边界（README §2）

- Skill 层（`SKILL.md`）：触发判断、流程编排、质检、措辞与合规。
- 引擎层（`scripts/`）：检索、打分、渲染等确定性逻辑；LLM 调用仅由 `summarize.py` 发起。
- 不要把检索/打分/渲染逻辑写进 `SKILL.md`，也不要把影响判断硬编码进引擎。

## 输出质量规则（README §10，任何生成物必须满足）

- 生成物必须满足 README §10 的四要素、红线与幻觉表述；不得隐藏缺口，也不得把「待核实」改写成确定结论。

## 结构与产物

- 目录树、产物清单见 README §6；中间产物字段契约见 §12.1（`schema_version`、`generated_at` 必备）。
- 密钥与产物边界见 README §11：`config/.env`、`output/` 不入库。
- 时间语义（UTC+8、次月 3 日前、30 天回溯）与去重规则见 README §8.1。

## 命令

当前本机开发使用 conda 环境 `py312`（`C:\Users\Cooper\miniconda3\envs\py312`，Python 3.12.13），不新建 `.venv`；安装 / 运行 / 测试命令均在该环境执行。未经确认不要新建 venv 或改用其他解释器。

```bash
conda activate py312
python -m pip install -r requirements.txt -r requirements-dev.txt
cp config/.env.example config/.env          # 填入搜索 / LLM Key
python scripts/run.py --client shenhua --month 2026-10 --format html,docx
python -m pytest tests/                     # 离线、必须全绿
python -m ruff check .                      # lint
python -m mypy                              # 类型检查（配置见 mypy.ini）
PYTHONUTF8=1 python -m pip_audit -r requirements.txt   # 依赖扫描（Windows 需 UTF-8 模式）
```

`pytest` 可离线运行且必须全绿；`run.py` 需要真实 Key 才能端到端执行。没有实际执行时不要声称通过。

## 修改约定

- 所有代码文件末尾必须有 `if __name__ == "__main__":` 演示块，直接展示该文件的核心用法（最小输入 → 调用主函数 → 打印结果）；演示不得依赖真实 Key 或联网，`python <文件路径>` 应能直接跑通。
- 依赖用 `==` 精确锁定（`requirements.txt` / `requirements-dev.txt`）；新增依赖需说明理由并跑依赖安全扫描。
- 提交前 `ruff check .`、`mypy`、`pytest`、`pip-audit` 必须通过；lint 规则集中在 `ruff.toml`，不要用逐行 `noqa` 绕过（`E402` 例外仅限 `sys.path` 引导的两个入口文件）。
- 文档保持 README 的 `§x.y` 编号稳定；新增/调整小节后 grep `§` 检查交叉引用。
- 验收口径见 README §14，不得放宽目标值。
- 测试覆盖范围见 README §13；新增行为先补测试。
- `tests/` 测试必须保持全绿，不得靠跳过维持绿灯；渲染变更需同步更新 `tests/snapshots/` 基线。

## 提交约定

- 提交信息用中文，格式 `type: 摘要`（docs / chore / feat / fix / test），摘要 ≤ 50 字。
- 一次提交只做一件事；文档与代码改动分开提交。
- 提交前确认不含密钥与 `output/` 产物。
- **写完代码自动提交**：代码改动完成且验证通过（单测 / 类型检查 / lint）后，直接本地提交，不必逐步询问；验证未通过不得提交，修复并复验后再提交。
- 自动提交仅限本地仓库；`git push`、改写已提交历史（amend / rebase）等操作仍需人工确认。