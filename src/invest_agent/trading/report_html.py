"""Standalone HTML presentation of the unchanged report Markdown."""
import html
import re

from markdown_it import MarkdownIt
from markdown_it.rules_block.table import escapedSplit


STYLE = """
body { max-width: 1200px; margin: 40px auto; padding: 0 28px;
       font-family: "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
       font-size: 16px; line-height: 1.75; color: #20242a; background: #fff; }
h1, h2, h3, h4 { line-height: 1.35; }
h2 { margin-top: 3rem; }
h3 { margin-top: 2.5rem; }
p, ul, ol { margin: 1rem 0; }
li { margin: .35rem 0; }
.table-scroll { overflow-x: auto; margin: 1.25rem 0; }
table { width: 100%; border-collapse: collapse; font-size: 14px;
        font-variant-numeric: tabular-nums; }
th, td { border: 1px solid #ccd2d9; padding: 9px 12px; text-align: left;
         min-width: 5em; max-width: 32em; overflow-wrap: break-word; }
th { background: #eef1f5; font-weight: 700; white-space: nowrap; }
td.numeric { text-align: right; white-space: nowrap; }
img { display: block; max-width: 100%; height: auto; margin: 1.5rem 0; }
pre { overflow-x: auto; padding: 1rem; background: #f5f6f8; }
code { overflow-wrap: anywhere; }
hr { border: 0; border-top: 1px solid #ccd2d9; margin: 3rem 0; }
"""


def _detached_row(state, start_line, end_line, silent):
    """Render pipe rows interrupted by evidence paragraphs, without moving text."""
    line = state.src[state.bMarks[start_line] + state.tShift[start_line]:state.eMarks[start_line]].strip()
    if state.is_code_block(start_line) or not (line.startswith("|") and line.endswith("|")):
        return False
    cells = escapedSplit(line[1:-1])
    if len(cells) < 2 or all(re.fullmatch(r":?-+:?", c.strip()) for c in cells):
        return False
    if silent:
        return True
    state.push("table_open", "table", 1)
    state.push("tbody_open", "tbody", 1)
    state.push("tr_open", "tr", 1)
    for cell in cells:
        state.push("td_open", "td", 1)
        inline = state.push("inline", "", 0)
        inline.content = cell.strip()
        inline.children = []
        state.push("td_close", "td", -1)
    state.push("tr_close", "tr", -1)
    state.push("tbody_close", "tbody", -1)
    state.push("table_close", "table", -1)
    state.line = start_line + 1
    return True


def render_html(markdown: str, title: str = "아침 검토") -> str:
    from .structure_narrative import user_text
    markdown = user_text(markdown)
    # Raw HTML is disabled: model prose stays text; Markdown images keep their paths.
    parser = MarkdownIt("commonmark", {"html": False}).enable("table")
    parser.block.ruler.after("table", "detached_row", _detached_row, {"alt": ["paragraph"]})
    tokens = parser.parse(markdown)
    for index, token in enumerate(tokens):
        if token.type == "td_open" and index + 1 < len(tokens):
            value = tokens[index + 1].content.strip()
            if re.fullmatch(r"[+−-]?(?:[0-9][0-9,]*)(?:\.[0-9]+)?(?:%|배|원| USD| KRW)?", value):
                token.attrJoin("class", "numeric")
    body = parser.renderer.render(tokens, parser.options, {})
    body = body.replace("<table>", '<div class="table-scroll"><table>')
    body = body.replace("</table>", "</table></div>")
    return ('<!doctype html>\n<html lang="ko"><head><meta charset="utf-8">'
            '<title>' + html.escape(title) + '</title><style>' + STYLE +
            '</style></head><body>\n' + body + '</body></html>\n')
