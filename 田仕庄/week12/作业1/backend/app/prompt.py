"""消防专家 Prompt（源自参考 notebook，改造为严格 JSON 输出）。"""

CATEGORY_HIGH = "高风险"
CATEGORY_MEDIUM = "中风险"
CATEGORY_LOW = "低风险"
CATEGORY_NONE = "无风险"
CATEGORY_NOT_HALLWAY = "非楼道"

VALID_CATEGORIES = (
    CATEGORY_HIGH,
    CATEGORY_MEDIUM,
    CATEGORY_LOW,
    CATEGORY_NONE,
    CATEGORY_NOT_HALLWAY,
)

SYSTEM_PROMPT = "你是一个专业消防专家，擅长识别各类场所的消防安全隐患。"

RISK_PROMPT = """请识别这张照片中楼道场景的消防安全隐患以及隐患的危险程度。根据楼道中是否存在大量堆积物、堆积物是否可燃以及是否有起火风险，将隐患分为以下五个类别：

- 高风险：楼道中出现电动车、电瓶、飞线充电等可能起火的元素。
- 中风险：楼道中存在大量堆积物严重影响通行，或堆放大量纸箱、木质家具等能造成火势蔓延的堵塞物。
- 低风险：存在楼道堆物现象但不严重。
- 无风险：楼道干净，无堆放物品。
- 非楼道：照片内容与楼道无关。

输出要求：
1. 只输出一个 JSON 对象，不要输出任何其他内容，不要使用 markdown 代码块。
2. JSON 格式为：{"category": "上述五个类别之一", "reason": "20到80字的判断依据", "evidence": ["观察到的关键元素1", "关键元素2"]}
3. category 字段必须严格使用上述五个类别名称之一。"""
