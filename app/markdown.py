"""Tiny, safe Markdown subset renderer (no third-party deps, no raw HTML passthrough).

Supports: paragraphs, # headings, fenced code blocks, - / * / 1. lists, pipe tables, > blockquotes,
`code`, **bold**, *italic* / _italic_, [text](http...) links and bare URLs.
Line breaks follow CommonMark: a single newline inside a paragraph is a soft wrap and renders as a space
(item YAML is hard-wrapped for the source, not for the screen); end a line with a backslash or two spaces
to force a line break, or leave a blank line to start a new paragraph.
Everything is HTML-escaped first, so item text can never inject markup.
"""

from __future__ import annotations

import html
import re

_CODE_SPAN = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_BARE_URL = re.compile(r"(?<![\"'=>])\b(https?://[^\s<]+[^\s<.,;:!?)\]'\"])")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*(?=\S)([^*]+?)(?<=\S)\*(?![\w*])|(?<![\w])_(?=\S)([^_]+?)(?<=\S)_(?![\w])")
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def inline(text: str) -> str:
    """Render inline markdown on raw (unescaped) text."""
    placeholders: list[str] = []

    def stash(fragment: str) -> str:
        placeholders.append(fragment)
        return f"\x00{len(placeholders) - 1}\x00"

    text = _CODE_SPAN.sub(lambda m: stash(f"<code>{html.escape(m.group(1), quote=False)}</code>"), text)
    text = _LINK.sub(lambda m: stash(
        f'<a href="{html.escape(m.group(2))}" target="_blank" rel="noopener noreferrer">'
        f"{html.escape(m.group(1), quote=False)}</a>"), text)
    text = _BARE_URL.sub(lambda m: stash(
        f'<a href="{html.escape(m.group(1))}" target="_blank" rel="noopener noreferrer">'
        f"{html.escape(m.group(1), quote=False)}</a>"), text)
    text = html.escape(text, quote=False)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(lambda m: f"<em>{m.group(1) or m.group(2)}</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: placeholders[int(m.group(1))], text)


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def render(text: str | None) -> str:
    if not text:
        return ""
    lines = str(text).replace("\r\n", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []
    i = 0

    def flush_para():
        if para:
            # Soft wraps join with a space; a trailing backslash or two spaces ends a visual line.
            lines_out: list[list[str]] = [[]]
            for n, raw in enumerate(para):
                seg = raw.rstrip()
                hard = n < len(para) - 1 and (raw.endswith("  ") or seg.endswith("\\"))
                if hard and seg.endswith("\\"):
                    seg = seg[:-1].rstrip()
                lines_out[-1].append(seg)
                if hard:
                    lines_out.append([])
            out.append("<p>" + "<br>\n".join(inline(" ".join(ln)) for ln in lines_out) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_para()
            lang = stripped[3:].strip()
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            cls = f' class="lang-{html.escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{html.escape(chr(10).join(body), quote=False)}</code></pre>")
            i += 1
            continue
        if not stripped:
            flush_para()
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            flush_para()
            level = min(6, len(m.group(1)) + 2)  # item headings sit below page headings
            out.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
            i += 1
            continue
        if "|" in stripped and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            flush_para()
            head = _split_row(stripped)
            rows = []
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            t = ["<div class=\"table-wrap\"><table><thead><tr>"]
            t += [f"<th>{inline(c)}</th>" for c in head]
            t.append("</tr></thead><tbody>")
            for r in rows:
                t.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
            t.append("</tbody></table></div>")
            out.append("".join(t))
            continue
        if stripped.startswith(">"):
            flush_para()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].lstrip())
                i += 1
            out.append(f"<blockquote>{render(chr(10).join(quote))}</blockquote>")
            continue
        m = _LIST_ITEM.match(line)
        if m and not para:
            ordered = m.group(2)[0].isdigit()
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            while i < len(lines):
                mm = _LIST_ITEM.match(lines[i])
                if mm and (mm.group(2)[0].isdigit()) == ordered:
                    items.append(mm.group(3))
                    i += 1
                elif items and lines[i].startswith(("  ", "\t")) and lines[i].strip():
                    items[-1] += " " + lines[i].strip()
                    i += 1
                else:
                    break
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        para.append(line.lstrip())  # keep trailing spaces: two of them mark a hard break
        i += 1
    flush_para()
    return "\n".join(out)


def plain_excerpt(text: str | None, limit: int = 180) -> str:
    """Strip markdown punctuation for short previews."""
    t = re.sub(r"```.*?```", " ", str(text or ""), flags=re.S)
    t = re.sub(r"[`*_#>|]", "", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = " ".join(t.split())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"
