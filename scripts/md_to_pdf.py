"""Convert docs/planning.md to docs/planning.pdf using xhtml2pdf."""
import pathlib
from io import BytesIO

import markdown
from xhtml2pdf import pisa

ROOT = pathlib.Path(__file__).resolve().parents[1]
src = ROOT / "docs" / "planning.md"
dest = ROOT / "docs" / "planning.pdf"

md_text = src.read_text(encoding="utf-8")
body = markdown.markdown(md_text, extensions=["tables", "fenced_code"])

CSS = """
@page {
    margin: 20mm 18mm 20mm 18mm;
    size: a4 portrait;
}
body {
    font-family: "Malgun Gothic", "Arial Unicode MS", sans-serif;
    font-size: 11pt;
    line-height: 1.7;
    color: #111;
}
h1 { font-size: 17pt; border-bottom: 2px solid #333; padding-bottom: 4px; margin-top: 0; }
h2 { font-size: 13pt; border-bottom: 1px solid #aaa; padding-bottom: 3px; margin-top: 20px; }
h3 { font-size: 11.5pt; margin-top: 14px; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 9.5pt; }
th { background-color: #f0f0f0; border: 1px solid #bbb; padding: 5px 7px; text-align: left; }
td { border: 1px solid #bbb; padding: 4px 7px; }
code { background: #f5f5f5; padding: 1px 3px; font-size: 9pt; }
pre { background: #f5f5f5; padding: 8px; font-size: 8.5pt; }
blockquote { border-left: 3px solid #aaa; margin: 0; padding-left: 10px; color: #555; }
hr { border: none; border-top: 1px solid #ddd; margin: 14px 0; }
"""

html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>{CSS}</style>
</head>
<body>{body}</body>
</html>"""

buf = BytesIO()
result = pisa.CreatePDF(html.encode("utf-8"), dest=buf, encoding="utf-8")

if result.err:
    print(f"오류: {result.err}")
else:
    dest.write_bytes(buf.getvalue())
    print(f"저장 완료: {dest}")
