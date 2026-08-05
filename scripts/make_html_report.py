"""把架构图 + 报告合并成单个 HTML 文件，图片用 base64 内嵌，手机浏览器可直接打开。"""
import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "deliverables" / "paperforge_architecture.png"
MD = ROOT / "deliverables" / "system_architecture_report.md"
OUT = ROOT / "deliverables" / "paperforge_architecture_report.html"

# 读取图片转 base64
img_data = IMG.read_bytes()
b64 = base64.b64encode(img_data).decode("ascii")
img_uri = f"data:image/png;base64,{b64}"

# 读取 markdown 报告，提取纯文本部分（去掉 SVG 代码块）
md_text = MD.read_text(encoding="utf-8")

# 简单 markdown → HTML 转换（不依赖第三方库）
import re

def md_to_html(md: str) -> str:
    lines = md.split("\n")
    html_lines = []
    in_code = False
    in_details_svg = False
    for line in lines:
        # 跳过 SVG 折叠块
        if "<details>" in line:
            in_details_svg = True
            continue
        if in_details_svg:
            if "</details>" in line:
                in_details_svg = False
            continue
        # 代码块
        if line.startswith("```"):
            if in_code:
                html_lines.append("</code></pre>")
                in_code = False
            else:
                lang = line[3:].strip()
                html_lines.append(f'<pre><code class="lang-{lang}">')
                in_code = True
            continue
        if in_code:
            html_lines.append(line.replace("<", "&lt;").replace(">", "&gt;"))
            continue
        # 标题
        m = re.match(r'^(#{1,6})\s+(.*)', line)
        if m:
            level = len(m.group(1))
            text = md_inline(m.group(2))
            html_lines.append(f"<h{level}>{text}</h{level}>")
            continue
        # 分隔线
        if line.strip() == "---":
            html_lines.append("<hr>")
            continue
        # 引用
        if line.startswith("> "):
            html_lines.append(f"<blockquote>{md_inline(line[2:])}</blockquote>")
            continue
        # 表格（简单处理）
        if line.startswith("|"):
            html_lines.append(f'<div class="table-row">{md_inline(line)}</div>')
            continue
        # 列表
        if re.match(r'^[-*]\s', line):
            html_lines.append(f"<li>{md_inline(line[2:])}</li>")
            continue
        # 空行
        if not line.strip():
            html_lines.append("<br>")
            continue
        # 普通段落
        html_lines.append(f"<p>{md_inline(line)}</p>")
    return "\n".join(html_lines)

def md_inline(text: str) -> str:
    # 行内代码
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    # 粗体
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    # 图片引用替换为 base64
    text = text.replace("![PaperForge v4.2 系统架构图](paperforge_architecture.png)",
                       f'<img src="{img_uri}" alt="架构图" style="width:100%;max-width:1200px;border:1px solid #ccc;"/>')
    # 普通图片引用
    text = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'<img src="\2" alt="\1" style="max-width:100%;"/>', text)
    return text

body = md_to_html(md_text)

# 完整 HTML
html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PaperForge v4.2 系统架构报告</title>
<style>
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    max-width: 900px;
    margin: 0 auto;
    padding: 20px;
    line-height: 1.6;
    color: #263238;
    font-size: 16px;
}}
h1 {{ color: #1A237E; border-bottom: 2px solid #1A237E; padding-bottom: 8px; font-size: 24px; }}
h2 {{ color: #1A237E; border-bottom: 1px solid #BBDEFB; padding-bottom: 4px; font-size: 20px; margin-top: 32px; }}
h3 {{ color: #283593; font-size: 18px; margin-top: 24px; }}
h4 {{ color: #303F9F; font-size: 16px; margin-top: 20px; }}
h5 {{ color: #303F9F; font-size: 15px; margin-top: 16px; }}
img {{ max-width: 100%; height: auto; display: block; margin: 16px auto; border: 1px solid #ccc; border-radius: 4px; }}
pre {{
    background: #F5F5F5;
    padding: 12px;
    border-radius: 4px;
    overflow-x: auto;
    font-size: 13px;
    line-height: 1.4;
}}
code {{
    background: #F5F5F5;
    padding: 2px 4px;
    border-radius: 2px;
    font-size: 14px;
    color: #C62828;
}}
pre code {{ background: none; padding: 0; color: #263238; }}
blockquote {{
    border-left: 4px solid #2196F3;
    margin: 12px 0;
    padding: 8px 16px;
    background: #E3F2FD;
    color: #1565C0;
}}
table {{
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0;
    font-size: 14px;
}}
th, td {{
    border: 1px solid #CFD8DC;
    padding: 8px 12px;
    text-align: left;
}}
th {{ background: #ECEFF1; font-weight: 600; }}
hr {{ border: none; border-top: 1px solid #CFD8DC; margin: 24px 0; }}
li {{ margin: 4px 0; }}
.table-row {{
    font-family: monospace;
    font-size: 13px;
    white-space: pre-wrap;
    color: #455A64;
}}
@media (max-width: 600px) {{
    body {{ padding: 12px; font-size: 14px; }}
    h1 {{ font-size: 20px; }}
    h2 {{ font-size: 18px; }}
    pre {{ font-size: 12px; }}
}}
</style>
</head>
<body>
{body}
</body>
</html>
"""

OUT.write_text(html, encoding="utf-8")
print(f"[ok] 已生成: {OUT}")
print(f"  大小: {OUT.stat().st_size // 1024} KB")
print(f"  图片 base64 内嵌，单文件可手机打开")
