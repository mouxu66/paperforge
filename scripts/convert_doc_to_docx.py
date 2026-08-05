"""用 Word COM 把 .doc 转成 .docx。

默认读取 ``PAPERFORGE_DOC_SOURCE_DIR``，也可通过 ``--source-dir`` 指定目录。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import win32com.client
except ImportError:
    print("需要 pywin32: pip install pywin32")
    sys.exit(1)


def parse_args() -> argparse.Namespace:
    default_dir = Path.home() / "Desktop" / "Word文档"
    parser = argparse.ArgumentParser(description="Convert legacy .doc files to .docx.")
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path(
            __import__("os").environ.get("PAPERFORGE_DOC_SOURCE_DIR", str(default_dir))
        ),
        help="Directory containing .doc files (default: PAPERFORGE_DOC_SOURCE_DIR or Desktop/Word文档).",
    )
    return parser.parse_args()


def main() -> int:
    source_dir = parse_args().source_dir.expanduser()
    if not source_dir.is_dir():
        print(f"源目录不存在或不是目录: {source_dir}")
        return 1

    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False
    doc_files = sorted(source_dir.glob("*.doc"))
    print(f"待转换 .doc 文件: {len(doc_files)}")

    converted = 0
    failed = 0
    try:
        for source in doc_files:
            try:
                doc = word.Documents.Open(str(source))
                output = source.with_suffix(".docx")
                doc.SaveAs2(str(output), FileFormat=16)
                doc.Close()
                converted += 1
                print(f"  [ok] {source.name} → {output.name}")
            except Exception as exc:  # noqa: BLE001 - report one bad document and continue
                print(f"  [fail] {source.name}: {exc}")
                failed += 1
    finally:
        word.Quit()

    print(f"\n转换完成: 成功 {converted}, 失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
