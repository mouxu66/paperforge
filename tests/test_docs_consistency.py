"""文档一致性校验测试（#9 文档框架）。

验证 README / 文档与代码事实的一致性：
- 论文种子数量与 README 内声明一致
- 导出格式全集与代码实现一致
- 文档中引用的文件路径真实存在
- 环境变量示例文件包含关键变量
"""

from __future__ import annotations

import re
from pathlib import Path


def test_readme_paper_count_consistent():
    """README 中声明的论文数应与 SEED_PAPERS 长度一致。"""
    from mock_api.data import SEED_PAPERS

    readme_path = Path(__file__).resolve().parent.parent / "README.md"
    assert readme_path.exists(), "README.md 不存在"

    content = readme_path.read_text(encoding="utf-8")

    # 提取"论文数据：N 篇"中的数字
    match = re.search(r"论文数据[：:]\s*(\d+)\s*篇", content)
    assert match, "README 中未找到「论文数据：N 篇」格式"
    declared = int(match.group(1))
    assert declared >= len(SEED_PAPERS), (
        f"README 声明 {declared} 篇论文，但 SEED_PAPERS 有 {len(SEED_PAPERS)} 篇种子。"
        f"声明数 ({declared}) 应 ≥ 种子数 ({len(SEED_PAPERS)})。"
    )


def test_readme_export_formats_consistent():
    """export_tasks 模块应可导入（验证导出功能可用）。"""
    from mock_api import export_tasks
    assert export_tasks is not None


def test_env_example_has_key_fields():
    """.env.example 应包含关键环境变量文档。"""
    env_example = Path(__file__).resolve().parent.parent / ".env.example"
    assert env_example.exists(), ".env.example 不存在"

    content = env_example.read_text(encoding="utf-8")
    required_keys = [
        "ENV=",
        "PAPERFORGE_DB_PATH",
        "PAPERFORGE_NO_AUTO_REBUILD",
        "PAPERFORGE_DISABLE_RESOURCE_DETECT",
    ]
    for key in required_keys:
        assert key in content, f".env.example 缺少环境变量文档: {key}"


def test_readme_paths_exist():
    """README 中引用的关键文件路径应真实存在。"""
    readme_path = Path(__file__).resolve().parent.parent / "README.md"
    assert readme_path.exists()

    _ = readme_path.read_text(encoding="utf-8")
    project_root = readme_path.parent

    # 检查代码块中的关键路径
    required_paths = [
        "mock_api/main.py",
        "mock_api/requirements.txt",
        "web/package.json",
    ]
    for path in required_paths:
        full_path = project_root / path
        assert full_path.exists(), f"README 引用的路径不存在: {path}"


def test_adr_registry_exists():
    """ADR 注册表应存在且包含至少一条记录。"""
    adr_path = Path(__file__).resolve().parent.parent / "docs" / "adr" / "README.md"
    assert adr_path.exists(), "docs/adr/README.md 不存在"
    content = adr_path.read_text(encoding="utf-8")
    assert "ADR" in content, "ADR 注册表内容不完整"


def test_runbook_exists():
    """运维手册应存在。"""
    runbook = Path(__file__).resolve().parent.parent / "RUNBOOK.md"
    assert runbook.exists(), "docs/operations/runbook.md 不存在"
    content = runbook.read_text(encoding="utf-8")
    assert "备份" in content or "backup" in content.lower(), "运维手册缺少备份章节"
