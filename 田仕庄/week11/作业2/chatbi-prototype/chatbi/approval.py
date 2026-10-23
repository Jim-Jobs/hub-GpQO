"""语义层审批工作流与版本管理。

生命周期
--------
    创建 / 修改 ──► 提交审批 ──► 审核通过 ──► 发布（旧版本自动 deprecated）
                       │
                       └──► 驳回 ──► rejected（可再次编辑后重新提交）

版本规则
--------
- 每次"新建草稿"产生 ``version = max_version + 1``；同一草稿的连续修改原地覆盖，不刷版本号。
- 待审批（pending）版本不可直接编辑，必须先驳回，避免绕过审计链。
- 回滚不物理删除历史：以目标版本的 payload 新建一个草稿，仍需走一次审批才能生效。
- 发布前会做一次「候选语义层」一致性校验（模型/指标/维度引用与 join 可达性）。
"""
from __future__ import annotations

from pathlib import Path

from . import config
from .semantic import SemanticError, build_registry, load_yaml_dicts


class ApprovalError(Exception):
    """审批流程非法操作或校验失败。"""


class SemanticService:
    """语义层对象的增删改查 + 审批 + 版本管理服务。"""

    def __init__(self, store, semantic_dir=None, actor: str = "admin"):
        self.store = store
        self.semantic_dir = Path(semantic_dir or config.SEMANTIC_DIR)
        self.actor = actor

    # ---------------- 迁移导入 ----------------

    def import_yaml(self, semantic_dir=None, actor: str | None = None) -> list[tuple]:
        """把 semantic 目录下的 YAML 定义幂等导入元数据库（已存在的对象跳过）。"""
        actor = actor or self.actor
        sd = Path(semantic_dir or self.semantic_dir)
        models, metrics, dims = load_yaml_dicts(sd)
        created = []
        release_id = None
        for obj_type, dicts in (("model", models), ("metric", metrics), ("dimension", dims)):
            for raw in dicts:
                name = raw["name"]
                if self.store.latest(obj_type, name):
                    continue
                if release_id is None:  # 整批导入属于同一个发布批次
                    release_id = self.store.create_release(
                        actor, f"从 YAML 迁移导入（{sd.name}）")
                ver = self.store.insert_version(
                    obj_type, name, raw, "published", actor, "从 YAML 迁移导入",
                    owner=raw.get("owner", ""), release_id=release_id,
                )
                self.store.add_event(obj_type, name, ver, "import", actor,
                                     f"来源 {sd.name}/{obj_type}s.yaml")
                created.append((obj_type, name, ver))
        if release_id is not None:
            self.store.publish_release(release_id)
        return created

    # ---------------- 编辑 ----------------

    def create(self, obj_type: str, name: str, payload: dict, actor: str | None = None,
               comment: str = "") -> int:
        if self.store.latest(obj_type, name):
            raise ApprovalError(f"{obj_type} {name} 已存在，如需变更请用 edit/rollback")
        return self.store.insert_version(obj_type, name, payload, "draft",
                                         actor or self.actor, comment,
                                         owner=payload.get("owner", ""))

    def edit(self, obj_type: str, name: str, patch: dict, actor: str | None = None,
             comment: str = "") -> int:
        actor = actor or self.actor
        work = self.store.working_payload(obj_type, name)
        if not work:
            raise ApprovalError(f"{obj_type} {name} 不存在")
        if work["status"] == "pending":
            raise ApprovalError(f"{name} v{work['version']} 正在审批中，请先驳回再编辑")
        payload = dict(work["payload"])
        payload.update(patch)
        if work["status"] == "draft":
            self.store.update_payload(obj_type, name, work["version"], payload, actor,
                                      comment or "修改草稿")
            return work["version"]
        return self.store.insert_version(obj_type, name, payload, "draft", actor,
                                         comment or "基于线上版本创建草稿",
                                         owner=payload.get("owner", ""),
                                         prev_version=work["version"])

    # ---------------- 审批 ----------------

    def submit(self, obj_type: str, name: str, actor: str | None = None,
               comment: str = "") -> int:
        actor = actor or self.actor
        latest = self.store.latest(obj_type, name)
        if not latest or latest["status"] != "draft":
            raise ApprovalError(f"{name} 无待提交的草稿版本")
        ver = latest["version"]
        self.store.update_status(obj_type, name, ver, "pending", actor, comment)
        self.store.add_event(obj_type, name, ver, "submit", actor, comment)
        return ver

    def approve(self, obj_type: str, name: str, actor: str | None = None,
                comment: str = "") -> int:
        actor = actor or self.actor
        latest = self.store.latest(obj_type, name)
        if not latest or latest["status"] != "pending":
            raise ApprovalError(f"{name} 没有处于待审批状态的版本")
        ver = latest["version"]
        self._validate_candidate(obj_type, name, ver)  # 发布前一致性守卫
        self.store.publish_version(
            obj_type, name, ver, actor, comment,
            release_note=f"{obj_type} {name} v{ver}",
        )
        self.store.add_event(obj_type, name, ver, "approve", actor, comment)
        return ver

    def reject(self, obj_type: str, name: str, actor: str | None = None,
               comment: str = "") -> int:
        actor = actor or self.actor
        latest = self.store.latest(obj_type, name)
        if not latest or latest["status"] != "pending":
            raise ApprovalError(f"{name} 没有处于待审批状态的版本")
        ver = latest["version"]
        self.store.update_status(obj_type, name, ver, "rejected", actor, comment)
        self.store.add_event(obj_type, name, ver, "reject", actor, comment)
        return ver

    # ---------------- 版本回滚 ----------------

    def rollback(self, obj_type: str, name: str, to_version: int, actor: str | None = None,
                 comment: str = "") -> int:
        actor = actor or self.actor
        src = self.store.version(obj_type, name, to_version)
        if not src:
            raise ApprovalError(f"{name} 不存在版本 v{to_version}")
        note = comment or f"回滚至 v{to_version}"
        ver = self.store.insert_version(obj_type, name, src["payload"], "draft", actor,
                                        note, owner=src.get("owner", ""),
                                        prev_version=to_version)
        self.store.add_event(obj_type, name, ver, "rollback", actor, note)
        return ver

    # ---------------- 校验 ----------------

    def _validate_candidate(self, obj_type: str, name: str, ver: int):
        """把待发布版本与线上已发布对象合成候选语义层，做一次一致性校验。"""
        if obj_type not in ("model", "metric", "dimension"):
            return
        pending = self.store.version(obj_type, name, ver)
        rows = self.store.active_published()
        buckets = {"model": [], "metric": [], "dimension": []}
        found = False
        for r in rows:
            if r["obj_type"] == obj_type and r["name"] == name:
                buckets[obj_type].append(pending["payload"])
                found = True
            else:
                buckets[r["obj_type"]].append(r["payload"])
        if not found:
            buckets[obj_type].append(pending["payload"])
        try:
            build_registry(buckets["model"], buckets["metric"], buckets["dimension"])
        except SemanticError as e:
            raise ApprovalError(f"发布校验未通过：{e}") from e

    # ---------------- 查询 ----------------

    def history(self, obj_type: str, name: str) -> dict:
        return {
            "versions": self.store.all_versions(obj_type, name),
            "events": self.store.events(obj_type, name),
        }

    def diff(self, obj_type: str, name: str, v1: int, v2: int) -> list[dict]:
        a = self.store.version(obj_type, name, v1)
        b = self.store.version(obj_type, name, v2)
        if not a or not b:
            raise ApprovalError("待比较的版本不存在")
        return _diff_payload(a["payload"], b["payload"])


def _diff_payload(a: dict, b: dict) -> list[dict]:
    """逐字段比较两个 payload，返回变更明细。"""
    changes = []
    for k in sorted(set(a) | set(b)):
        av, bv = a.get(k), b.get(k)
        if av != bv:
            changes.append({"field": k, "from": av, "to": bv})
    return changes
