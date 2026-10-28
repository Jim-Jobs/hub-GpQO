"""分类解析逻辑单元测试（不依赖模型 API）。"""
import pytest

from app.classifier import ClassificationError, parse_model_output


class TestJsonParsing:
    def test_clean_json(self):
        text = '{"category": "高风险", "reason": "楼道内停放电动车并飞线充电", "evidence": ["电动车", "插线板"]}'
        result = parse_model_output(text)
        assert result.category == "高风险"
        assert result.level == 4
        assert "电动车" in result.evidence
        assert result.reason

    def test_json_in_code_fence(self):
        text = '```json\n{"category": "无风险", "reason": "楼道干净整洁"}\n```'
        result = parse_model_output(text)
        assert result.category == "无风险"
        assert result.level == 1

    def test_json_embedded_in_text(self):
        text = '分析结果如下：{"category": "中风险", "reason": "大量纸箱堆积"} 以上。'
        result = parse_model_output(text)
        assert result.category == "中风险"
        assert result.level == 3

    def test_evidence_string_normalized_to_list(self):
        text = '{"category": "低风险", "reason": "少量杂物", "evidence": "纸箱"}'
        result = parse_model_output(text)
        assert result.evidence == ["纸箱"]

    def test_evidence_truncated_to_8(self):
        text = '{"category": "高风险", "reason": "x", "evidence": ' + \
            str([f"e{i}" for i in range(12)]).replace("'", '"') + '}'
        result = parse_model_output(text)
        assert len(result.evidence) == 8


class TestCategoryNormalization:
    @pytest.mark.parametrize("alias,expected", [
        ("无隐患", "无风险"),
        ("中等风险", "中风险"),
        ("高危", "高风险"),
        ("不是楼道", "非楼道"),
    ])
    def test_aliases(self, alias, expected):
        text = f'{{"category": "{alias}", "reason": "测试"}}'
        assert parse_model_output(text).category == expected

    def test_category_with_suffix(self):
        text = '{"category": "高风险（发现电动车充电）", "reason": "电动车"}'
        assert parse_model_output(text).category == "高风险"

    def test_not_hallway_level(self):
        text = '{"category": "非楼道", "reason": "这是一张风景照"}'
        result = parse_model_output(text)
        assert result.level == 0


class TestKeywordFallback:
    def test_free_text_fallback(self):
        result = parse_model_output("该楼道堆放大量纸箱和木质家具，属于中风险场景。")
        assert result.category == "中风险"

    def test_fallback_prefers_specific(self):
        # 文本同时含"高风险"与"风险"，应命中"高风险"
        result = parse_model_output("存在飞线充电，判定为高风险")
        assert result.category == "高风险"


class TestFailures:
    def test_empty_raises(self):
        with pytest.raises(ClassificationError):
            parse_model_output("   ")

    def test_garbage_raises(self):
        with pytest.raises(ClassificationError):
            parse_model_output("hello world 12345")

    def test_json_without_valid_category_falls_back_then_raises(self):
        with pytest.raises(ClassificationError):
            parse_model_output('{"category": "未知类别", "reason": "test"}')
