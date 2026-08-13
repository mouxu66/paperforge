"""CONFIG_MISMATCH（code_audit.py）单测。"""
from __future__ import annotations

from mock_api.experiment_audit.code_audit import (
    check_config_mismatch,
    extract_config_values,
)


def _write_config(tmp_path, content: str):
    (tmp_path / "config.py").write_text(content, encoding="utf-8")
    return str(tmp_path)


def test_extract_config_values_reads_py_config(tmp_path):
    repo = _write_config(
        tmp_path,
        "lr = 3e-4\nbatch_size = 16\nepochs = 200\noptimizer = 'adamw'\n",
    )
    values = extract_config_values(repo)
    assert values["learning_rate"] == "3e-4"
    assert values["batch_size"] == "16"
    assert values["epochs"] == "200"
    assert values["optimizer"] == "adamw"


def test_mismatch_reported_when_both_sides_differ(tmp_path):
    repo = _write_config(tmp_path, "lr = 3e-4\nbatch_size = 16\nepochs = 200\n")
    text = "We train with learning rate 1e-4, batch size 64 for 300 epochs."
    findings = check_config_mismatch(text, repo)
    keys = {f["title"] for f in findings}
    assert any("learning_rate" in t for t in keys)
    assert any("batch_size" in t for t in keys)
    assert any("epochs" in t for t in keys)
    assert all(f["type"] == "CONFIG_MISMATCH" for f in findings)
    assert all(f["needs_human_review"] is True for f in findings)


def test_matching_values_produce_no_finding(tmp_path):
    repo = _write_config(tmp_path, "lr = 3e-4\nbatch_size = 64\n")
    text = "We use learning rate 3e-4 and batch size 64."
    assert check_config_mismatch(text, repo) == []


def test_missing_side_skips(tmp_path):
    # 代码有 seed，但论文没提 seed → 跳过（不误报）
    repo = _write_config(tmp_path, "seed = 42\n")
    assert check_config_mismatch("We train a transformer model.", repo) == []


def test_no_config_files_returns_empty(tmp_path):
    assert check_config_mismatch("learning rate 1e-4", str(tmp_path)) == []


def test_yaml_config_is_scanned(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "learning_rate: 1e-3\nbatch_size: 8\n", encoding="utf-8"
    )
    values = extract_config_values(str(tmp_path))
    assert values["learning_rate"] == "1e-3"
    assert values["batch_size"] == "8"
