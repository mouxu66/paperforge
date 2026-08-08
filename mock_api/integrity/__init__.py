"""文献/引用完整性校验子模块（ADR-014 · P4）。

暴露 :mod:`mock_api.integrity.citation_verifier` 中的
:func:`verify_citations` 与 :func:`assess_citation_integrity` 供 DEPTH 与
reflection 流水线调用。所有接口均 fail-open：任何异常都不会向上抛出，
而是返回带 ``status="unknown"`` 的安全结果。
"""
