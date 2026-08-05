"""全库数据健康检查脚本（scripts/check_data_health.py）分析函数测试。

覆盖 2026-08-03 发现的各类 JSON 脏数据检测与归一化：
- 双重编码 / 字面 'null' 字符串 / 类型不匹配 / 逐字符数组 / 引号元素 / 空元素
- NULL 合规性（以 ORM nullable 为准）
- 向量列维度与类型检测
- --fix 的归一化逻辑
"""

from __future__ import annotations

from scripts.check_data_health import (
    _normalize_value,
    analyze_json_value,
    analyze_vector_value,
)

# ---------------------------------------------------------------------------
# analyze_json_value
# ---------------------------------------------------------------------------


def codes(issues: list[tuple[str, str]]) -> list[str]:
    return [c for c, _ in issues]


class TestAnalyzeJsonValue:
    def test_healthy_list_passthrough(self):
        raw = '["Alice", "Bob"]'
        assert analyze_json_value(raw, "list", table="t", column="c") == []

    def test_healthy_dict_passthrough(self):
        raw = '{"claims": [], "validated": []}'
        assert analyze_json_value(raw, "dict", table="t", column="c") == []

    def test_healthy_nested_list(self):
        raw = '[{"id": "E1", "content": "x"}, {"id": "E2"}]'
        assert analyze_json_value(raw, "list", table="t", column="c") == []

    def test_double_encoded(self):
        raw = r'"[\"Alice\", \"Bob\"]"'
        assert codes(analyze_json_value(raw, "list", table="t", column="c")) == [
            "DOUBLE_ENCODED"
        ]

    def test_literal_null_string(self):
        raw = "null"
        assert codes(analyze_json_value(raw, "dict", table="t", column="c")) == [
            "JSON_NULL"
        ]

    def test_null_violation_when_not_nullable(self):
        assert codes(
            analyze_json_value(None, "list", table="t", column="c", nullable=False)
        ) == ["NULL_VIOLATION"]

    def test_null_ok_when_nullable(self):
        assert analyze_json_value(None, "list", table="t", column="c", nullable=True) == []

    def test_plain_comma_string_in_list_column(self):
        raw = "Alice, Bob, Carol"
        assert codes(analyze_json_value(raw, "list", table="t", column="c")) == [
            "NOT_JSON"
        ]

    def test_type_mismatch_list_column_stores_dict(self):
        raw = '{"a": 1}'
        assert "TYPE_MISMATCH" in codes(
            analyze_json_value(raw, "list", table="t", column="c")
        )

    def test_type_mismatch_dict_column_stores_list(self):
        raw = "[1, 2, 3]"
        assert codes(analyze_json_value(raw, "dict", table="t", column="c")) == [
            "TYPE_MISMATCH"
        ]

    def test_char_array(self):
        # ['[', '"', 'A', 'l', 'i', 'c', 'e', '"', ']'] → 拼接为 '["Alice"]' 可解析
        raw = '["[", "\\"", "A", "l", "i", "c", "e", "\\"", "]"]'
        assert codes(analyze_json_value(raw, "list", table="t", column="c")) == [
            "CHAR_ARRAY"
        ]

    def test_short_initial_list_not_char_array(self):
        """合法的单字母/短字符串列表不应被误报为逐字符数组。"""
        assert analyze_json_value('["A", "B", "C"]', "list", table="t", column="c") == []
        assert analyze_json_value('["en", "zh"]', "list", table="t", column="c") == []

    def test_char_array_without_brackets_not_flagged(self):
        """拼接后无法解析为结构化 JSON 的短串列表 → 不标记（也无法修复）。"""
        assert analyze_json_value('["A", "B"]', "list", table="t", column="c") == []

    def test_quoted_element(self):
        raw = '["Alice", "\\"Bob\\""]'
        assert "QUOTED_ELEMENT" in codes(
            analyze_json_value(raw, "list", table="t", column="c")
        )

    def test_empty_element(self):
        raw = '["Alice", "", "   "]'
        assert codes(analyze_json_value(raw, "list", table="t", column="c")) == [
            "EMPTY_ELEMENT",
            "EMPTY_ELEMENT",
        ]

    def test_empty_string_value(self):
        assert codes(analyze_json_value("", "list", table="t", column="c")) == ["EMPTY"]

    def test_quoted_json_string_in_list_column(self):
        # '"Edward J. Hu, Yelong Shen"' → JSON 字符串而非数组
        raw = '"Edward J. Hu, Yelong Shen"'
        assert codes(analyze_json_value(raw, "list", table="t", column="c")) == [
            "TYPE_MISMATCH"
        ]

    def test_unknown_type_expect_any_is_lenient(self):
        raw = '{"anything": true}'
        assert analyze_json_value(raw, "any", table="t", column="c") == []
        raw2 = "[1, 2, 3]"
        assert analyze_json_value(raw2, "any", table="t", column="c") == []

    def test_invalid_json_in_any_column(self):
        raw = "not json at all"
        assert codes(analyze_json_value(raw, "any", table="t", column="c")) == [
            "NOT_JSON"
        ]

    def test_invalid_json_in_list_column(self):
        raw = "[[[unclosed"
        assert codes(analyze_json_value(raw, "list", table="t", column="c")) == [
            "NOT_JSON"
        ]


# ---------------------------------------------------------------------------
# analyze_vector_value
# ---------------------------------------------------------------------------


class TestAnalyzeVectorValue:
    def test_healthy_vector(self):
        vec = "[0.1, 0.2, 0.3]"
        assert analyze_vector_value(vec, expected_dim=3) == []

    def test_null_allowed_when_nullable(self):
        assert analyze_vector_value(None, nullable=True) == []

    def test_null_violation_when_not_nullable(self):
        assert codes(analyze_vector_value(None, nullable=False)) == ["NULL_VIOLATION"]

    def test_wrong_dimension(self):
        vec = "[0.1, 0.2]"
        assert codes(analyze_vector_value(vec, expected_dim=384)) == ["EMBEDDING_DIM"]

    def test_non_numeric_elements(self):
        vec = "[0.1, \"abc\", 0.3]"
        assert codes(analyze_vector_value(vec, expected_dim=3)) == ["VECTOR_TYPE"]

    def test_not_json(self):
        assert codes(analyze_vector_value("hello")) == ["NOT_JSON"]

    def test_not_array(self):
        assert codes(analyze_vector_value('{"a": 1}')) == ["VECTOR_TYPE"]

    def test_empty_string(self):
        assert codes(analyze_vector_value("  ")) == ["EMPTY"]


# ---------------------------------------------------------------------------
# _normalize_value（--fix 归一化逻辑）
# ---------------------------------------------------------------------------


class TestNormalizeValue:
    def test_literal_null_to_sql_null(self):
        value, fixable = _normalize_value("null", "dict")
        assert fixable is True
        assert value is None

    def test_double_encoded_unwrap(self):
        value, fixable = _normalize_value(r'"[\"Alice\", \"Bob\"]"', "list")
        assert fixable is True
        assert value == ["Alice", "Bob"]

    def test_comma_string_split(self):
        value, fixable = _normalize_value("Alice, Bob, Carol", "list")
        assert fixable is True
        assert value == ["Alice", "Bob", "Carol"]

    def test_clean_value_untouched(self):
        value, fixable = _normalize_value('["Alice"]', "list")
        assert fixable is True
        assert value == ["Alice"]

    def test_quoted_elements_cleaned(self):
        value, fixable = _normalize_value('["\\"Alice\\""]', "list")
        assert fixable is True
        assert value == ["Alice"]

    def test_empty_elements_removed(self):
        value, fixable = _normalize_value('["Alice", "", "  "]', "list")
        assert fixable is True
        assert value == ["Alice"]

    def test_dict_passthrough(self):
        value, fixable = _normalize_value('{"a": 1}', "dict")
        assert fixable is True
        assert value == {"a": 1}

    def test_unfixable_none_input(self):
        value, fixable = _normalize_value(None, "list")
        assert fixable is False
        assert value is None

    def test_unfixable_invalid_json_in_dict(self):
        value, fixable = _normalize_value("not json", "dict")
        assert fixable is False
        assert value is None

    def test_char_array_rebuild(self):
        raw = '["[", "\\"", "A", "l", "i", "c", "e", "\\"", "]"]'
        value, fixable = _normalize_value(raw, "list")
        assert fixable is True
        assert value == ["Alice"]

    def test_type_mismatch_dict_column_list_not_fixable(self):
        """dict 列存 list：清洗后仍是错误类型 → 不可修复（避免 --fix 误导）。"""
        value, fixable = _normalize_value("[1, 2, 3]", "dict")
        assert fixable is False
        assert value is None

    def test_type_mismatch_list_column_dict_not_fixable(self):
        value, fixable = _normalize_value('{"a": 1}', "list")
        assert fixable is False
        assert value is None

    def test_double_encoded_into_dict_column(self):
        """dict 列的双重编码仍可解包修复。"""
        value, fixable = _normalize_value(r'"{\"a\": 1}"', "dict")
        assert fixable is True
        assert value == {"a": 1}
