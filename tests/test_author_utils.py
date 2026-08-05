"""parse_authors 统一解析工具测试。

覆盖 2026-08-03 全库作者乱码修复的三种历史存储格式：
1. 标准 JSON 数组文本（ORM 读出 list）
2. 双重编码字符串（json.dumps 写入 JSON 列导致）
3. 引号包裹的逗号分隔串
"""

from __future__ import annotations

from mock_api.author_utils import parse_authors


class TestParseAuthors:
    def test_none_returns_empty(self):
        assert parse_authors(None) == []

    def test_empty_string_returns_empty(self):
        assert parse_authors("") == []
        assert parse_authors("   ") == []

    def test_proper_list_passthrough(self):
        assert parse_authors(["Alice", "Bob"]) == ["Alice", "Bob"]

    def test_json_array_string(self):
        raw = '["Tim Dettmers", "Artidoro Pagnoni"]'
        assert parse_authors(raw) == ["Tim Dettmers", "Artidoro Pagnoni"]

    def test_double_encoded_string(self):
        """双重编码：JSON 字符串内嵌套数组（本次修复的核心场景）。

        注意：真实脏数据里内层引号带反斜杠转义（r-string 保留）。
        """
        raw = r'"[\"Cosmin Pohoata\"]"'
        assert parse_authors(raw) == ["Cosmin Pohoata"]

    def test_double_encoded_multiple_authors(self):
        raw = r'"[\"Michele Liberatore\", \"Massimo Riccaboni\"]"'
        assert parse_authors(raw) == ["Michele Liberatore", "Massimo Riccaboni"]

    def test_quoted_comma_string(self):
        """引号包裹的逗号分隔串。"""
        raw = '"Edward J. Hu, Yelong Shen, Phillip Wallis"'
        assert parse_authors(raw) == ["Edward J. Hu", "Yelong Shen", "Phillip Wallis"]

    def test_plain_comma_string(self):
        assert parse_authors("Alice, Bob, Carol") == ["Alice", "Bob", "Carol"]

    def test_list_containing_json_array_string(self):
        assert parse_authors(['["A", "B"]']) == ["A", "B"]

    def test_strips_whitespace_and_skips_empty(self):
        assert parse_authors([" Alice ", "", "  ", "Bob"]) == ["Alice", "Bob"]

    def test_single_plain_name(self):
        assert parse_authors("just a name") == ["just a name"]

    def test_non_string_non_list_returns_empty(self):
        assert parse_authors(123) == []
        assert parse_authors({}) == []
        assert parse_authors([]) == []

    def test_quoted_comma_list_without_brackets(self):
        """无外层方括号、逗号分隔的多个 JSON 字符串 → 解包引号。"""
        assert parse_authors('"Alice","Bob"') == ["Alice", "Bob"]

    def test_char_array_defense(self):
        """逐字符拆分的历史脏数据：先 join 再解析。"""
        raw = ["[", '"', "C", "o", "s", "m", "i", "n", " ", "P", "o", "h", "o", "a", "t", "a", '"', "]"]
        assert parse_authors(raw) == ["Cosmin Pohoata"]

    def test_list_with_quoted_elements(self):
        """列表元素本身是带引号的 JSON 字符串。"""
        assert parse_authors(['"Alice"', '"Bob"']) == ["Alice", "Bob"]

    def test_roundtrip_via_orm_double_encoded(self, db_session):
        """通过 ORM 写入双重编码字符串，验证读取后能还原人名（模拟真实损坏数据）。"""
        from mock_api.models import Paper

        paper = Paper(
            id="test_double",
            title="T",
            authors=r'"[\"Cosmin Pohoata\"]"',
            abstract="",
            category="all",
            tags=[],
            year=2026,
        )
        db_session.add(paper)
        db_session.commit()
        db_session.expire_all()

        loaded = db_session.query(Paper).filter(Paper.id == "test_double").first()
        assert loaded is not None
        assert parse_authors(loaded.authors) == ["Cosmin Pohoata"]

    def test_roundtrip_via_orm_quoted_comma(self, db_session):
        """引号逗号串经 ORM 读取后解析。"""
        from mock_api.models import Paper

        paper = Paper(
            id="test_comma",
            title="T",
            authors='"Edward J. Hu, Yelong Shen"',
            abstract="",
            category="all",
            tags=[],
            year=2026,
        )
        db_session.add(paper)
        db_session.commit()
        db_session.expire_all()

        loaded = db_session.query(Paper).filter(Paper.id == "test_comma").first()
        assert loaded is not None
        assert parse_authors(loaded.authors) == ["Edward J. Hu", "Yelong Shen"]
