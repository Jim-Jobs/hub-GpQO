"""语义层平台 CLI：迁库、审批、版本管理。

用法示例
--------
    python semantic_admin.py init                      # 建库并把 YAML 迁移进元数据库
    python semantic_admin.py list                      # 列出全部语义层对象（最新版本）
    python semantic_admin.py show metric gmv           # 查看对象某个版本的完整定义
    python semantic_admin.py edit metric gmv --set display_name=销售额(GMV) --actor alice
    python semantic_admin.py submit metric gmv --actor alice
    python semantic_admin.py approve metric gmv --actor dba --comment 复核通过
    python semantic_admin.py reject metric gmv --actor dba --comment 口径需与财务对齐
    python semantic_admin.py rollback metric gmv --to 1 --actor alice
    python semantic_admin.py history metric gmv
    python semantic_admin.py diff metric gmv --from 1 --to 2
    python semantic_admin.py ddl --dialect postgres    # 输出 PostgreSQL 建表 DDL
    python semantic_admin.py demo                      # 临时库端到端演示（不污染正式库）
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

# 允许以脚本方式直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chatbi import config  # noqa: E402
from chatbi.approval import ApprovalError, SemanticService  # noqa: E402
from chatbi.metastore import MetaStore, ddl  # noqa: E402
from chatbi.semantic import SemanticError, load_registry_from_db  # noqa: E402


# ---------------- 输出工具 ----------------

def out(msg: str = ""):
    print(msg)


def hr(title: str = ""):
    out("-" * 68)
    if title:
        out(title)


def _coerce(text: str):
    """--set k=v 的 v 尽量解析为 JSON 值（数字/布尔/数组），否则按字符串。"""
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text


def _kv(pairs):
    result = {}
    for item in pairs or []:
        if "=" not in item:
            raise SystemExit(f"--set 参数需形如 key=value，收到：{item}")
        k, v = item.split("=", 1)
        result[k.strip()] = _coerce(v)
    return result


def _open_store(args) -> MetaStore:
    path = Path(args.meta_db) if args.meta_db else config.META_DB
    if not path.exists() and args.cmd != "init":
        out(f"[提示] 元数据库不存在：{path}，可先执行 init 建库迁移。")
    store = MetaStore(path)
    store.init_schema()
    return store


def _service(args, store) -> SemanticService:
    return SemanticService(store, semantic_dir=args.semantic_dir, actor=args.actor)


def _status_mark(status: str) -> str:
    return {
        "draft": "草稿",
        "pending": "待审批",
        "published": "已发布",
        "rejected": "已驳回",
        "deprecated": "已下线",
    }.get(status, status)


# ---------------- 子命令实现 ----------------

def cmd_init(args):
    store = _open_store(args)
    svc = _service(args, store)
    created = svc.import_yaml()
    out(f"元数据库：{store.db_path}")
    out(f"迁移完成，新增对象 {len(created)} 个：")
    for obj_type, name, ver in created:
        out(f"  + {obj_type:<9} {name:<16} v{ver} 已发布")
    out(f"当前元数据库对象总数：{len(store.list_objects())}")
    store.close()


def cmd_ddl(args):
    out(ddl(args.dialect))


def cmd_list(args):
    store = _open_store(args)
    rows = store.list_objects(args.type)
    hr(f"语义层对象（{len(rows)} 个，各显示最新版本）")
    out(f"{'类型':<10}{'名称':<18}{'版本':<6}{'状态':<8}{'负责人':<12}显示名")
    for r in rows:
        out(f"{r['obj_type']:<10}{r['name']:<18}{'v'+str(r['version']):<6}"
            f"{_status_mark(r['status']):<8}{r.get('owner','') or '-':<12}"
            f"{r['payload'].get('display_name','')}")
    store.close()


def cmd_show(args):
    store = _open_store(args)
    row = (store.version(args.type, args.name, args.version) if args.version
           else store.working_payload(args.type, args.name))
    if not row:
        raise SystemExit(f"未找到 {args.type} {args.name}")
    hr(f"{args.type} {args.name}  v{row['version']}  [{_status_mark(row['status'])}]")
    out(json.dumps(row["payload"], ensure_ascii=False, indent=2))
    store.close()


def cmd_create(args):
    store = _open_store(args)
    svc = _service(args, store)
    payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
    ver = svc.create(args.type, args.name, payload, comment=args.comment)
    out(f"已创建草稿 {args.type} {args.name} v{ver}（状态：待提交审批）")
    store.close()


def cmd_edit(args):
    store = _open_store(args)
    svc = _service(args, store)
    patch = _kv(args.set)
    if args.file:
        patch.update(json.loads(Path(args.file).read_text(encoding="utf-8")))
    if not patch:
        raise SystemExit("请通过 --set 或 --file 提供要修改的字段")
    ver = svc.edit(args.type, args.name, patch, comment=args.comment)
    out(f"已提交修改：{args.type} {args.name} v{ver}  变更字段 {list(patch)}")
    store.close()


def cmd_submit(args):
    store = _open_store(args)
    svc = _service(args, store)
    ver = svc.submit(args.type, args.name, comment=args.comment)
    out(f"已提交审批：{args.type} {args.name} v{ver} → 待审批")
    store.close()


def cmd_approve(args):
    store = _open_store(args)
    svc = _service(args, store)
    ver = svc.approve(args.type, args.name, comment=args.comment)
    row = store.version(args.type, args.name, ver)
    rel = store.release(row["release_id"]) if row and row["release_id"] else None
    batch = f"，发布批次 {rel['label']}" if rel else ""
    out(f"审批通过：{args.type} {args.name} v{ver} → 已发布（历史版本已下线{batch}）")
    store.close()


def cmd_reject(args):
    store = _open_store(args)
    svc = _service(args, store)
    ver = svc.reject(args.type, args.name, comment=args.comment)
    out(f"已驳回：{args.type} {args.name} v{ver}（可修改后重新提交）")
    store.close()


def cmd_rollback(args):
    store = _open_store(args)
    svc = _service(args, store)
    ver = svc.rollback(args.type, args.name, args.to, comment=args.comment)
    out(f"已生成回滚草稿：{args.type} {args.name} v{ver}（内容取自 v{args.to}，仍需审批）")
    store.close()


def cmd_history(args):
    store = _open_store(args)
    svc = _service(args, store)
    hist = svc.history(args.type, args.name)
    hr(f"{args.type} {args.name} 版本历史")
    out(f"{'版本':<8}{'状态':<9}{'创建人':<10}{'创建时间':<20}审核人")
    for v in hist["versions"]:
        out(f"{'v'+str(v['version']):<8}{_status_mark(v['status']):<9}"
            f"{v.get('created_by','') or '-':<10}{v.get('created_at',''):<20}"
            f"{v.get('reviewed_by','') or '-'}")
    hr("审计事件")
    out(f"{'版本':<8}{'动作':<12}{'操作人':<10}{'时间':<20}备注")
    for e in hist["events"]:
        out(f"{'v'+str(e['version']):<8}{e['action']:<12}{e.get('actor','') or '-':<10}"
            f"{e.get('created_at',''):<20}{e.get('comment','')}")
    store.close()


def cmd_diff(args):
    store = _open_store(args)
    svc = _service(args, store)
    changes = svc.diff(args.type, args.name, args.__dict__["from"], args.to)
    hr(f"{args.type} {args.name}：v{args.__dict__['from']} → v{args.to}")
    if not changes:
        out("两个版本定义完全一致。")
    for c in changes:
        out(f"  {c['field']}:\n    - {c['from']}\n    + {c['to']}")
    store.close()


def cmd_demo(args):
    """在临时库中演示完整审批/版本流程，不污染正式库。"""
    tmp = Path(tempfile.mkdtemp(prefix="chatbi_meta_demo_"))
    meta_path = tmp / "chatbi_meta.db"
    store = MetaStore(meta_path)
    store.init_schema()
    svc = SemanticService(store, semantic_dir=args.semantic_dir, actor="alice")

    out(f"演示元数据库（临时）：{meta_path}")
    hr("① YAML → 元数据库迁移")
    created = svc.import_yaml(actor="migrator")
    out(f"导入对象 {len(created)} 个；总量 {len(store.list_objects())}")

    hr("② 修改指标并提交审批")
    v2 = svc.edit("metric", "gmv", {"display_name": "销售额（GMV）"},
                  actor="alice", comment="统一对外展示口径")
    out(f"alice 修改 gmv → 草稿 v{v2}")
    svc.submit("metric", "gmv", actor="alice", comment="请数据治理组复核")
    out("alice 提交审批 → 待审批")

    hr("③ 审批通过并发布（旧版本自动下线）")
    svc.approve("metric", "gmv", actor="dba", comment="口径确认无误")
    out("dba 审批通过 → 已发布")
    for v in store.all_versions("metric", "gmv"):
        out(f"  v{v['version']:<3} {_status_mark(v['status']):<7} {v['payload'].get('display_name')}")

    hr("④ 驳回流程：待审批版本不可直接编辑")
    v3 = svc.edit("metric", "order_cnt", {"description": "订单数（含退货）"},
                  actor="bob", comment="补充说明")
    svc.submit("metric", "order_cnt", actor="bob")
    try:
        svc.edit("metric", "order_cnt", {"description": "偷偷改"}, actor="bob")
    except ApprovalError as e:
        out(f"  拦截：{e}")
    svc.reject("metric", "order_cnt", actor="dba", comment="描述与口径不符，请重写")
    out(f"dba 驳回 order_cnt v{v3} → 已驳回（可再次编辑）")

    hr("⑤ 版本回滚：不删除历史，新建草稿重新审批")
    v4 = svc.rollback("metric", "gmv", to_version=1, actor="alice", comment="回滚展示名")
    svc.submit("metric", "gmv", actor="alice")
    svc.approve("metric", "gmv", actor="dba", comment="回滚确认")
    out(f"gmv 回滚后发布 v{v4}；历史版本仍可查询：")
    for v in store.all_versions("metric", "gmv"):
        out(f"  v{v['version']:<3} {_status_mark(v['status'])}")

    hr("⑥ 发布前一致性守卫：引用不存在的指标应被拦截")
    bad = {
        "name": "bad_metric", "display_name": "非法指标", "metric_type": "composite",
        "model": "fact_invoice", "expression": "${gmv} / ${not_exist}", "synonyms": [],
        "time_model": "fact_invoice", "time_dimension": "month", "time_column": "i.InvoiceDate",
        "available_dimensions": ["month"], "default_filters": [], "description": "",
    }
    svc.create("metric", "bad_metric", bad, actor="alice")
    svc.submit("metric", "bad_metric", actor="alice")
    try:
        svc.approve("metric", "bad_metric", actor="dba")
    except ApprovalError as e:
        out(f"  拦截：{e}")

    hr("⑦ 版本差异对比（v2 → 回滚后的 v3）")
    for c in svc.diff("metric", "gmv", 2, v4):
        out(f"  {c['field']}: {c['from']} → {c['to']}")

    hr("⑧ 运行时从元数据库加载「已发布」语义层")
    reg = load_registry_from_db(store)
    out(f"已加载指标 {len(reg.metrics)} 个 / 维度 {len(reg.dimensions)} 个 / 模型 {len(reg.models)} 个")
    out(f"gmv 显示名（已生效）：{reg.metric_display('gmv')}")
    out(f"gmv 定义：{reg.metric('gmv').expression}")

    store.close()
    out()
    out(f"演示完成，临时库保留于 {tmp}（可直接删除）")


# ---------------- 参数解析 ----------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="semantic_admin", description="ChatBI 语义层管理平台（迁库 / 审批 / 版本）")
    p.add_argument("--meta-db", help="元数据库路径，默认取 config.META_DB")
    p.add_argument("--semantic-dir", help="YAML 语义层目录，默认取 config.SEMANTIC_DIR")
    p.add_argument("--actor", default=None, help="操作人标识，默认 admin")

    # 让 --meta-db / --semantic-dir / --actor 也可写在子命令之后（dest 加后缀避免覆盖全局值）
    leaf = argparse.ArgumentParser(add_help=False)
    leaf.add_argument("--meta-db", dest="meta_db_sub", default=None, help=argparse.SUPPRESS)
    leaf.add_argument("--semantic-dir", dest="semantic_dir_sub", default=None, help=argparse.SUPPRESS)
    leaf.add_argument("--actor", dest="actor_sub", default=None, help="操作人标识，默认 admin")

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", parents=[leaf], help="初始化元数据库并从 YAML 迁移")

    d = sub.add_parser("ddl", parents=[leaf], help="输出建表 DDL")
    d.add_argument("--dialect", default="sqlite", help="sqlite | postgres")

    ls = sub.add_parser("list", parents=[leaf], help="列出语义层对象")
    ls.add_argument("--type", choices=["model", "metric", "dimension"])

    sh = sub.add_parser("show", parents=[leaf], help="查看对象定义")
    sh.add_argument("type", choices=["model", "metric", "dimension"])
    sh.add_argument("name")
    sh.add_argument("--version", type=int, default=None, help="指定版本，默认取编辑基线")

    cr = sub.add_parser("create", parents=[leaf], help="新建对象草稿")
    cr.add_argument("type", choices=["model", "metric", "dimension"])
    cr.add_argument("name")
    cr.add_argument("--file", required=True, help="对象定义 JSON 文件")
    cr.add_argument("--comment", default="")

    ed = sub.add_parser("edit", parents=[leaf], help="修改对象（生成/更新草稿）")
    ed.add_argument("type", choices=["model", "metric", "dimension"])
    ed.add_argument("name")
    ed.add_argument("--set", action="append", metavar="K=V", help="字段=值，可重复")
    ed.add_argument("--file", help="JSON 补丁文件")
    ed.add_argument("--comment", default="")

    for name, help_text in (("submit", "提交审批"), ("approve", "审批通过并发布"),
                            ("reject", "驳回待审批版本")):
        s = sub.add_parser(name, parents=[leaf], help=help_text)
        s.add_argument("type", choices=["model", "metric", "dimension"])
        s.add_argument("name")
        s.add_argument("--comment", default="")

    rb = sub.add_parser("rollback", parents=[leaf], help="回滚到指定版本（新建草稿）")
    rb.add_argument("type", choices=["model", "metric", "dimension"])
    rb.add_argument("name")
    rb.add_argument("--to", type=int, required=True)
    rb.add_argument("--comment", default="")

    hi = sub.add_parser("history", parents=[leaf], help="查看版本历史与审计事件")
    hi.add_argument("type", choices=["model", "metric", "dimension"])
    hi.add_argument("name")

    df = sub.add_parser("diff", parents=[leaf], help="比较两个版本")
    df.add_argument("type", choices=["model", "metric", "dimension"])
    df.add_argument("name")
    df.add_argument("--from", type=int, required=True, dest="from")
    df.add_argument("--to", type=int, required=True)

    sub.add_parser("demo", parents=[leaf], help="临时库端到端演示")
    return p


HANDLERS = {
    "init": cmd_init, "ddl": cmd_ddl, "list": cmd_list, "show": cmd_show,
    "create": cmd_create, "edit": cmd_edit, "submit": cmd_submit,
    "approve": cmd_approve, "reject": cmd_reject, "rollback": cmd_rollback,
    "history": cmd_history, "diff": cmd_diff, "demo": cmd_demo,
}


def main(argv=None):
    args = build_parser().parse_args(argv)
    # 合并「写在子命令之后」的同名参数
    args.meta_db = getattr(args, "meta_db_sub", None) or args.meta_db
    args.semantic_dir = getattr(args, "semantic_dir_sub", None) or args.semantic_dir
    args.actor = getattr(args, "actor_sub", None) or args.actor or "admin"
    try:
        HANDLERS[args.cmd](args)
    except (ApprovalError, SemanticError) as e:
        out(f"[失败] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
