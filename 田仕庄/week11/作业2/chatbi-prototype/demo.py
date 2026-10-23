"""演示脚本：跑通 NL → DSL → SQL → 结果 全链路，并演示权限与受控校验。

用法：
    python demo.py              # 批量演示
    python demo.py -i           # 交互式问数
    python demo.py --user bob   # 指定用户（演示权限）
"""
from __future__ import annotations

import sys

from chatbi import compiler
from chatbi.pipeline import ChatBI, format_result
from chatbi.semantic import ValidationError

NL_QUESTIONS = [
    "总销售额是多少",
    "近3个月销售额",
    "各月销售额趋势",
    "各大区销售额",
    "销售额Top5的国家",
    "北美销售额",
    "北美和南美哪个销售额高",
    "客单价是多少",
    "2009年各季度销售额",
    "按曲风统计销量",
    "上个月订单数",
    "客户数最多的国家",
    "销售额环比",
    "销售额和订单数分别是多少",   # 指标歧义 -> 澄清
    "今天天气怎么样",             # 无指标 -> 澄清
]


def section(title: str):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def run_batch(chatbi: ChatBI, user_id: str, questions=None):
    questions = questions or NL_QUESTIONS
    section(f"① 全链路问数（user={user_id}，数据锚点日={chatbi.anchor}）")
    for q in questions:
        print(format_result(chatbi.ask(q, user_id=user_id)))
        print("-" * 78)


def run_permission(chatbi: ChatBI):
    section("② 权限演示：行级过滤 + 列级拒绝 + 列级脱敏")
    cases = [
        ("bob", "各大区销售额"),      # 行级规则：只应看到「北美」
        ("bob", "各城市销售额"),      # 列级拒绝：无 city 权限
        ("carol", "各城市销售额"),    # 列级脱敏：city 显示 ******
    ]
    for user_id, q in cases:
        print(f"（{user_id}）", end=" ")
        print(format_result(chatbi.ask(q, user_id=user_id)))
        print("-" * 78)


def run_validation(chatbi: ChatBI):
    section("③ 受控词表校验：非法取值在编译期被拦截")
    bad_dsl = {"metric": "gmv", "dimensions": ["region"],
               "filters": [{"dimension": "region", "op": "eq", "value": "东北"}]}
    try:
        compiler.compile_dsl(bad_dsl, chatbi.registry, chatbi.user("alice"), chatbi.anchor)
    except ValidationError as e:
        print(f"  DSL: {bad_dsl}")
        print(f"  拦截: {e.error}")
        print(f"  提示: {e.hint}")


def run_multiturn(chatbi: ChatBI, user_id: str = "alice"):
    section("④ 多轮会话演示：上下文改写（省略补全 / 下钻 / 指代消解 / 澄清接续 / 不污染）")
    chatbi.sessions.clear()
    turns = [
        "各大区销售额",         # 建立上下文
        "那上个月呢",           # borrow：省略指标，只换时间
        "换成订单数呢",         # followup：换指标，继承维度与时间
        "那按城市看",           # borrow：下钻到城市
        "这些城市的销售额",     # anaphora：用上一轮结果里的城市取值做筛选
        "总销售额是多少",       # new：全新问题，不继承
        "今天天气怎么样",       # 无指标且非追问 -> 澄清（上下文不污染）
    ]
    sid = None
    for q in turns:
        res = chatbi.chat(q, user_id=user_id, session_id=sid)
        sid = res["session_id"]
        ctx = res.get("context") or {}
        print(f"[轮次 {res['turn']}] 上下文改写: {ctx.get('mode')} | {ctx.get('note')}")
        print(format_result(res))
        print("-" * 78)
    sess = chatbi.sessions.get(sid)
    print(f"会话 {sid[:8]}… 共 {len(sess.turns)} 轮；存储统计: {chatbi.sessions.stats()}")
    chatbi.reset(sid)
    print("已 reset 会话（清空上下文）。")


def run_interactive(chatbi: ChatBI, user_id: str):
    section(f"交互式问数（user={user_id}）。输入 exit 退出")
    while True:
        try:
            q = input("问题> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in ("exit", "quit"):
            break
        print(format_result(chatbi.ask(q, user_id=user_id)))


def main():
    args = sys.argv[1:]
    user_id = "alice"
    if "--user" in args:
        user_id = args[args.index("--user") + 1]

    chatbi = ChatBI(use_llm=False)
    print(f"语义层已加载（来源={chatbi.semantic_source}）：{len(chatbi.registry.metrics)} 个指标 / "
          f"{len(chatbi.registry.dimensions)} 个维度 / "
          f"{len(chatbi.registry.models)} 个模型")
    print(f"数据源: {chatbi.db_path}")

    if "-i" in args or "--interactive" in args:
        run_interactive(chatbi, user_id)
        return

    run_batch(chatbi, user_id)
    run_permission(chatbi)
    run_validation(chatbi)
    run_multiturn(chatbi, user_id)


if __name__ == "__main__":
    main()
