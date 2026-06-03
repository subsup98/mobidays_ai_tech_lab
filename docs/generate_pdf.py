"""planning.md → planning.pdf  (ReportLab Platypus + 맑은고딕)"""
import re
import sys
import os

sys.stdout.reconfigure(encoding="utf-8")

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer,
    Table, TableStyle, Preformatted,
    HRFlowable, ListFlowable, ListItem,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── 폰트 등록 ──────────────────────────────────────────────
FONT_NORMAL = r"C:\Windows\Fonts\malgun.ttf"
FONT_BOLD   = r"C:\Windows\Fonts\malgunbd.ttf"

pdfmetrics.registerFont(TTFont("Malgun", FONT_NORMAL))
pdfmetrics.registerFont(TTFont("Malgun-Bold", FONT_BOLD))
pdfmetrics.registerFontFamily(
    "Malgun",
    normal="Malgun",
    bold="Malgun-Bold",
    italic="Malgun",
    boldItalic="Malgun-Bold",
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MD_PATH  = os.path.join(BASE_DIR, "planning.md")
PDF_PATH = os.path.join(BASE_DIR, "planning.pdf")

# ── 스타일 정의 ────────────────────────────────────────────
def S(name, **kw):
    base = dict(fontName="Malgun", fontSize=10, leading=16, spaceAfter=4, spaceBefore=2)
    base.update(kw)
    return ParagraphStyle(name, **base)

ST = {
    "h1":       S("h1",  fontName="Malgun-Bold", fontSize=16, leading=22,
                  spaceAfter=6, spaceBefore=18),
    "h2":       S("h2",  fontName="Malgun-Bold", fontSize=13, leading=18,
                  spaceAfter=5, spaceBefore=14),
    "h3":       S("h3",  fontName="Malgun-Bold", fontSize=11, leading=15,
                  spaceAfter=4, spaceBefore=10),
    "body":     S("body"),
    "bq":       S("bq",  leftIndent=14, textColor=colors.HexColor("#555555")),
    "li":       S("li",  spaceBefore=1, spaceAfter=1),
    "cell":     S("cell", fontSize=9, leading=13, spaceAfter=0, spaceBefore=0),
    "cell_hdr": S("cell_hdr", fontName="Malgun-Bold", fontSize=9,
                  leading=13, spaceAfter=0, spaceBefore=0),
    "code":     ParagraphStyle(
                    "code", fontName="Malgun", fontSize=8.5, leading=13,
                    backColor=colors.HexColor("#F4F4F4"),
                    leftIndent=0, spaceBefore=6, spaceAfter=6,
                ),
}

# ── 인라인 마크업 변환 ──────────────────────────────────────
def _esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

_INLINE = re.compile(r'\*\*`([^`]+)`\*\*|\*\*(.+?)\*\*|`([^`]+)`')

def markup(text):
    """마크다운 인라인(볼드·코드)을 ReportLab XML로 변환.
    우선순위: **`code`** > **bold** > `code`
    """
    out = []
    last = 0
    for m in _INLINE.finditer(text):
        out.append(_esc(text[last:m.start()]))
        if m.group(1) is not None:          # **`code`**
            out.append(f'<b><font name="Courier" size="9">{_esc(m.group(1))}</font></b>')
        elif m.group(2) is not None:        # **bold**
            out.append(f'<b>{_esc(m.group(2))}</b>')
        else:                               # `code`
            out.append(f'<font name="Courier" size="9">{_esc(m.group(3))}</font>')
        last = m.end()
    out.append(_esc(text[last:]))
    return "".join(out)

# ── 테이블 생성 ────────────────────────────────────────────
def make_table(lines):
    rows_raw = [l for l in lines if not re.match(r"^\|[\s\-|]+\|$", l)]
    parsed = []
    for r in rows_raw:
        cells = [c.strip() for c in r.strip().strip("|").split("|")]
        parsed.append(cells)
    if not parsed:
        return None

    col_n   = max(len(r) for r in parsed)
    col_w   = (A4[0] - 4.4 * cm) / col_n
    tdata   = []
    for ri, row in enumerate(parsed):
        while len(row) < col_n:
            row.append("")
        style = ST["cell_hdr"] if ri == 0 else ST["cell"]
        tdata.append([Paragraph(markup(c), style) for c in row])

    t = Table(tdata, colWidths=[col_w] * col_n, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  colors.HexColor("#EEEEEE")),
        ("GRID",          (0, 0), (-1, -1), 0.5, colors.HexColor("#BBBBBB")),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1),
         [colors.white, colors.HexColor("#FAFAFA")]),
    ]))
    return t

# ── 마크다운 파서 ──────────────────────────────────────────
def parse(md_text):
    flow = []
    lines = md_text.splitlines()
    i = 0

    def peek(offset=0):
        idx = i + offset
        return lines[idx] if idx < len(lines) else ""

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # H1
        if line.startswith("# "):
            flow.append(Paragraph(markup(line[2:]), ST["h1"]))
            flow.append(HRFlowable(width="100%", thickness=1,
                                   color=colors.HexColor("#AAAAAA"),
                                   spaceBefore=2, spaceAfter=6))
            i += 1

        # H2
        elif line.startswith("## "):
            flow.append(Paragraph(markup(line[3:]), ST["h2"]))
            flow.append(HRFlowable(width="100%", thickness=0.5,
                                   color=colors.HexColor("#CCCCCC"),
                                   spaceBefore=2, spaceAfter=4))
            i += 1

        # H3
        elif line.startswith("### "):
            flow.append(Paragraph(markup(line[4:]), ST["h3"]))
            i += 1

        # HR
        elif stripped == "---":
            flow.append(HRFlowable(width="100%", thickness=0.5,
                                   color=colors.HexColor("#CCCCCC"),
                                   spaceBefore=8, spaceAfter=8))
            i += 1

        # Blockquote
        elif line.startswith("> "):
            flow.append(Paragraph(markup(line[2:]), ST["bq"]))
            i += 1

        # Fenced code block
        elif stripped.startswith("```"):
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1
            flow.append(Preformatted("\n".join(code_lines), ST["code"]))

        # Table
        elif stripped.startswith("|"):
            tbl_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                tbl_lines.append(lines[i].strip())
                i += 1
            t = make_table(tbl_lines)
            if t:
                flow.append(t)
                flow.append(Spacer(1, 4))

        # Bullet list (- or *)
        elif re.match(r"^[-*] ", line):
            items = []
            while i < len(lines) and re.match(r"^[-*] ", lines[i]):
                txt = markup(lines[i][2:])
                items.append(ListItem(Paragraph(txt, ST["li"]),
                                      bulletText="•", leftIndent=12))
                i += 1
            flow.append(ListFlowable(items, bulletType="bullet",
                                     leftIndent=12,
                                     bulletFontName="Malgun",
                                     spaceBefore=2, spaceAfter=4))

        # Numbered list
        elif re.match(r"^\d+\. ", line):
            items = []
            while i < len(lines) and re.match(r"^\d+\. ", lines[i]):
                m = re.match(r"^\d+\. (.+)", lines[i])
                items.append(ListItem(Paragraph(markup(m.group(1)), ST["li"])))
                i += 1
            flow.append(ListFlowable(items, bulletType="1",
                                     leftIndent=15,
                                     bulletFontName="Malgun",
                                     spaceBefore=2, spaceAfter=4))

        # Empty line
        elif not stripped:
            flow.append(Spacer(1, 4))
            i += 1

        # Normal paragraph (merge consecutive plain lines)
        else:
            para_parts = []
            while i < len(lines):
                l = lines[i]
                s = l.strip()
                if (not s or l.startswith("#") or s == "---"
                        or s.startswith("|") or s.startswith("```")
                        or l.startswith("> ")
                        or re.match(r"^[-*] ", l)
                        or re.match(r"^\d+\. ", l)):
                    break
                para_parts.append(l.strip())
                i += 1
            if para_parts:
                flow.append(Paragraph(markup(" ".join(para_parts)), ST["body"]))

    return flow


def main():
    with open(MD_PATH, encoding="utf-8") as f:
        md_text = f.read()

    doc = SimpleDocTemplate(
        PDF_PATH,
        pagesize=A4,
        leftMargin=2.2 * cm,
        rightMargin=2.2 * cm,
        topMargin=2.0 * cm,
        bottomMargin=2.0 * cm,
        title="기획안 — 모비데이즈 회의 액션아이템 자동화 시스템",
    )
    doc.build(parse(md_text))
    print(f"PDF 생성 완료: {PDF_PATH}")


if __name__ == "__main__":
    main()
