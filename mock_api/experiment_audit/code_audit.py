"""P1-1 代码配置 vs 论文超参比对（CONFIG_MISMATCH 落地）。

独立于 PDF：用户提供代码仓库本地路径（克隆后目录），与论文全文比对超参。
与 data_leakage 一样是端点驱动，不接入 run_paper_audit（pipeline 无代码仓库来源）。

确定性规则（不做语义判断）：
1. 扫描仓库内常见 config 文件，逐行提取超参键值。
2. 从论文文本提取同键的超参值（保守：只匹配清晰的 "key of value" / "key = value" 形式）。
3. 仅当「代码有值 且 论文有值 且 不一致」才产出 Finding——单侧缺失不误报。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .schemas import make_finding

logger = logging.getLogger(__name__)

# 常见配置文件 glob（相对 repo_dir 递归匹配文件名）
CONFIG_GLOBS = (
    "config.py",
    "config.yaml",
    "config.yml",
    "config.json",
    "hparams.py",
    "defaults.py",
    "args.py",
    "arguments.py",
    "train.sh",
    "run.sh",
    "*.yaml",
    "*.yml",
)

# canonical key → 代码文件中的别名（正则转义后用于 "alias = value" 匹配）
_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "learning_rate": ("learning_rate", "lr", "learning rate"),
    "batch_size": ("batch_size", "batchsize", "batch size"),
    "epochs": ("epochs", "num_epochs", "n_epochs", "max_epochs"),
    "weight_decay": ("weight_decay", "weight decay"),
    "seed": ("seed", "random_seed", "manual_seed"),
    "dropout": ("dropout", "drop_rate"),
    "hidden_dim": ("hidden_dim", "hidden_size", "hidden_dimension"),
    "num_layers": ("num_layers", "n_layers", "nlayer"),
    "optimizer": ("optimizer",),
}

# 论文文本中的值模式（按 canonical key，保守匹配清晰措辞）
_PAPER_VALUE_RES: dict[str, re.Pattern] = {
    "learning_rate": re.compile(
        r"(?:learning rate|lr)\s*(?:of|is|=|:)?\s*([0-9]+\.[0-9]+[eE][-+]?[0-9]+|[0-9]+[eE][-+]?[0-9]+|[0-9]+\.[0-9]+)",
        re.IGNORECASE,
    ),
    "batch_size": re.compile(r"batch\s*size\s*(?:of|is|=|:)?\s*(\d+)", re.IGNORECASE),
    "epochs": re.compile(r"(\d+)\s*epochs?", re.IGNORECASE),
    "weight_decay": re.compile(
        r"weight\s*decay\s*(?:of|is|=|:)?\s*([0-9]+\.[0-9]+[eE][-+]?[0-9]+|[0-9]+[eE][-+]?[0-9]+|[0-9]+\.[0-9]+)",
        re.IGNORECASE,
    ),
    "seed": re.compile(r"(?:random\s*seed|seed)\s*(?:of|is|=|:)?\s*(\d+)", re.IGNORECASE),
    "dropout": re.compile(r"dropout\s*(?:of|is|=|:)?\s*([0-9]+\.[0-9]+)", re.IGNORECASE),
    "hidden_dim": re.compile(r"hidden\s*(?:dim|size)\s*(?:of|is|=|:)?\s*(\d+)", re.IGNORECASE),
    "num_layers": re.compile(
        r"(?:number of layers|layers)\s*(?:of|is|=|:)?\s*(\d+)", re.IGNORECASE
    ),
    "optimizer": re.compile(r"optimizer\s*(?:of|is|=|:)?\s*([A-Za-z]+)", re.IGNORECASE),
}

# 相对容差（数值比较）：差距超此比例视为不一致
_REL_TOLERANCE = 0.05

# 递归扫描时跳过的目录（避免 node_modules/.git 等大目录拖慢扫描）
_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        "dist",
        "build",
        ".eggs",
    }
)

# 最大递归深度（防止超深目录结构导致极慢扫描）
_MAX_DEPTH = 5


def _find_config_files(repo_dir: str) -> list[Path]:
    """递归收集仓库内常见 config 文件，限制深度并跳过常见大目录。"""
    root = Path(repo_dir)
    if not root.is_dir():
        return []
    out: list[Path] = []
    try:
        for p in root.rglob("*"):
            # 跳过常见大目录
            if any(skip in p.parts for skip in _SKIP_DIRS):
                continue
            # 限制递归深度
            depth = len(p.relative_to(root).parts)
            if depth > _MAX_DEPTH:
                continue
            if not p.is_file():
                continue
            if p.name in CONFIG_GLOBS or p.suffix.lower() in (".yaml", ".yml"):
                out.append(p)
    except OSError as e:
        logger.debug("[audit] 配置文件目录遍历失败: %s - %s", repo_dir, e)
        return []
    return out


def _extract_code_value(key: str, line: str) -> str | None:
    """从单行代码提取 canonical key 的赋值值（首个命中，去引号）。"""
    for alias in _KEY_ALIASES[key]:
        m = re.search(
            r"\b" + re.escape(alias) + r"\s*[=:]\s*(.+?)\s*(?:#.*)?$",
            line,
            re.IGNORECASE,
        )
        if m:
            return m.group(1).strip().strip("'\"")
    return None


def extract_config_values(repo_dir: str) -> dict[str, str]:
    """扫描仓库配置，返回 canonical key → 代码中的值（字符串原样）。"""
    values: dict[str, str] = {}
    for path in _find_config_files(repo_dir):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError as e:
            logger.debug("[audit] 配置文件读取失败: %s - %s", path, e)
            continue
        for line in lines:
            for key in _KEY_ALIASES:
                if key in values:
                    continue
                v = _extract_code_value(key, line)
                if v is not None:
                    values[key] = v
    return values


def _extract_paper_value(full_text: str, key: str) -> str | None:
    pattern = _PAPER_VALUE_RES.get(key)
    if pattern is None:
        return None
    m = pattern.search(full_text or "")
    return m.group(1).strip() if m else None


def _normalize(value: str) -> float | str:
    v = (value or "").strip().strip("'\"").lower()
    if not v:
        return ""
    try:
        return float(v)
    except ValueError:
        return v


def _values_equal(a: str, b: str) -> bool:
    na, nb = _normalize(a), _normalize(b)
    if isinstance(na, float) and isinstance(nb, float):
        return abs(na - nb) <= max(1e-9, abs(nb) * _REL_TOLERANCE)
    return na == nb


def check_config_mismatch(full_text: str, repo_dir: str) -> list[dict]:
    """比对论文超参与代码配置，产出 CONFIG_MISMATCH 列表。

    两侧都找到且不一致才报告；任一侧缺失跳过（防误报）。
    """
    code_values = extract_config_values(repo_dir)
    if not code_values:
        return []
    findings: list[dict] = []
    for key, code_val in code_values.items():
        paper_val = _extract_paper_value(full_text, key)
        if paper_val is None:
            continue
        if _values_equal(code_val, paper_val):
            continue
        findings.append(
            make_finding(
                "CONFIG_MISMATCH",
                title=f"论文超参 {key} 与代码默认值不一致",
                claim=f"论文: {key} = {paper_val}",
                computed=f"代码: {key} = {code_val}",
                method="论文文本正则抽取 vs 仓库 config 文件扫描",
                normal_explanation=(
                    "代码可能后于论文更新，或论文报告的是调优后配置而非默认值；需核对实际训练脚本"
                ),
                needs_human_review=True,
            )
        )
    return findings
