from pathlib import Path
import re
import sys

# Files known to contain catch (e: any) in production source.
FILES = [
    "web/src/api/ask.ts",
    "web/src/api/depth.ts",
    "web/src/pages/AskPage.tsx",
    "web/src/pages/ComparePapersPage.tsx",
    "web/src/pages/DepthAnalysis.tsx",
    "web/src/pages/DepthReview.tsx",
    "web/src/pages/DetailPage.tsx",
    "web/src/components/DevTools.tsx",
    "web/src/components/ModelPresets.tsx",
    "web/src/components/PaperList.tsx",
    "web/src/components/PDFViewer.tsx",
    "web/src/components/ReflectionList.tsx",
    "web/src/components/ReflectionResultView.tsx",
    "web/src/components/ReflectionUpload.tsx",
    "web/src/components/TagManager.tsx",
    "web/src/components/UploadPaper.tsx",
    "web/src/components/writing/CslExportPanel.tsx",
]

PATTERN = re.compile(r"^[\r]?([ \t]*)\}\s*catch\s*\(\s*(\w+)\s*:\s*any\s*\)\s*\{", re.MULTILINE)
ERR_TYPE = "Error & { response?: { status?: number; data?: { detail?: string } } }"


def replace_catch(text: str) -> str:
    linesep = "\r\n" if "\r\n" in text else "\n"

    def repl(match: re.Match) -> str:
        indent = match.group(1)
        var = match.group(2)
        return f"{indent}}} catch (err0: unknown) {{{linesep}{indent}  const {var} = err0 as {ERR_TYPE};"

    return PATTERN.sub(repl, text)


def main() -> int:
    for path_str in FILES:
        path = Path(path_str)
        if not path.exists():
            print(f"skip missing file: {path}")
            continue
        text = path.read_text(encoding="utf-8")
        new_text = replace_catch(text)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            print(f"fixed: {path}")
        else:
            print(f"no change: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
