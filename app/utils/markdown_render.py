"""把版本说明 Markdown 转成可安全插入页面的 HTML。

先抽出围栏代码，再整体转义，最后只插入白名单标签，避免原稿里的 HTML / 脚本生效。
"""
from __future__ import annotations

import html
import re
from urllib.parse import urlparse

_FENCE_RE = re.compile(r'```([\w+-]*)[ \t]*\n(.*?)```', re.S)
_HEADING_RE = re.compile(r'^(#{1,6})[ \t]+(.+?)\s*#*\s*$')
_HR_RE = re.compile(r'^([-*_])\1{2,}\s*$')
_UL_RE = re.compile(r'^[-*+][ \t]+(.+)$')
_OL_RE = re.compile(r'^\d+[.)][ \t]+(.+)$')
_TABLE_SEP_RE = re.compile(r'^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$')
_INLINE_CODE_RE = re.compile(r'`([^`]+)`')
_BOLD_RE = re.compile(r'\*\*(.+?)\*\*|__(.+?)__')
_ITALIC_RE = re.compile(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)')
_STRIKE_RE = re.compile(r'~~(.+?)~~')
_LINK_RE = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
_ALLOWED_SCHEMES = {'http', 'https', 'mailto'}


def markdown_to_html(source: str) -> str:
    text = str(source or '').replace('\r\n', '\n').replace('\r', '\n')
    fences: list[str] = []

    def _keep_fence(match: re.Match) -> str:
        fences.append(match.group(2))
        return f'\n\n@@FENCE{len(fences) - 1}@@\n\n'

    text = _FENCE_RE.sub(_keep_fence, text)
    text = html.escape(text, quote=False)
    return _render_blocks(text, fences)


def _safe_href(raw: str) -> str:
    url = html.unescape((raw or '').strip())
    if not url or any(ord(ch) < 32 for ch in url):
        return ''
    if url.startswith('//') or '\\' in url or '"' in url or "'" in url:
        return ''
    parsed = urlparse(url)
    scheme = (parsed.scheme or '').lower()
    if scheme in _ALLOWED_SCHEMES:
        return html.escape(url, quote=True)
    if scheme:
        return ''
    if url.startswith('#') or (url.startswith('/') and not url.startswith('//')):
        return html.escape(url, quote=True)
    return ''


def _render_inline(text: str) -> str:
    codes: list[str] = []

    def _keep_code(match: re.Match) -> str:
        codes.append(f'<code>{match.group(1)}</code>')
        return f'@@CODE{len(codes) - 1}@@'

    text = _INLINE_CODE_RE.sub(_keep_code, text)

    def _link(match: re.Match) -> str:
        label = match.group(1)
        href = _safe_href(match.group(2))
        if not href:
            return label
        return f'<a href="{href}" rel="noopener noreferrer">{label}</a>'

    text = _LINK_RE.sub(_link, text)
    text = _BOLD_RE.sub(lambda m: f'<strong>{m.group(1) or m.group(2)}</strong>', text)
    text = _STRIKE_RE.sub(r'<del>\1</del>', text)
    text = _ITALIC_RE.sub(lambda m: f'<em>{m.group(1) or m.group(2)}</em>', text)

    for idx, snippet in enumerate(codes):
        text = text.replace(f'@@CODE{idx}@@', snippet)
    return text


def _split_table_row(line: str) -> list[str]:
    row = line.strip()
    if row.startswith('|'):
        row = row[1:]
    if row.endswith('|'):
        row = row[:-1]
    return [cell.strip() for cell in row.split('|')]


def _is_table_sep(line: str) -> bool:
    return bool(_TABLE_SEP_RE.match(line.strip()))


def _render_blocks(text: str, fences: list[str]) -> str:
    lines = text.split('\n')
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue

        fence_match = re.fullmatch(r'@@FENCE(\d+)@@', stripped)
        if fence_match:
            idx = int(fence_match.group(1))
            body = html.escape(fences[idx], quote=False) if 0 <= idx < len(fences) else ''
            out.append(f'<pre><code>{body}</code></pre>')
            i += 1
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            level = len(heading.group(1))
            out.append(f'<h{level}>{_render_inline(heading.group(2))}</h{level}>')
            i += 1
            continue

        if _HR_RE.match(stripped):
            out.append('<hr>')
            i += 1
            continue

        if stripped.startswith('&gt;'):
            quote_lines: list[str] = []
            while i < n:
                cur = lines[i].strip()
                if not cur.startswith('&gt;'):
                    break
                quote_lines.append(cur[4:].lstrip() if cur.startswith('&gt; ') else cur[4:])
                i += 1
            inner = _render_inline(' '.join(quote_lines))
            out.append(f'<blockquote><p>{inner}</p></blockquote>')
            continue

        if i + 1 < n and '|' in stripped and _is_table_sep(lines[i + 1]):
            headers = _split_table_row(stripped)
            i += 2
            body_rows: list[list[str]] = []
            while i < n and '|' in lines[i] and lines[i].strip():
                if _is_table_sep(lines[i]):
                    i += 1
                    continue
                body_rows.append(_split_table_row(lines[i].strip()))
                i += 1
            thead = ''.join(f'<th>{_render_inline(cell)}</th>' for cell in headers)
            tbody = []
            for row in body_rows:
                padded = row + [''] * max(0, len(headers) - len(row))
                tds = ''.join(f'<td>{_render_inline(cell)}</td>' for cell in padded[:len(headers)])
                tbody.append(f'<tr>{tds}</tr>')
            out.append(
                '<table><thead><tr>'
                + thead
                + '</tr></thead><tbody>'
                + ''.join(tbody)
                + '</tbody></table>'
            )
            continue

        ul_item = _UL_RE.match(stripped)
        ol_item = _OL_RE.match(stripped)
        if ul_item or ol_item:
            ordered = bool(ol_item)
            tag = 'ol' if ordered else 'ul'
            items: list[str] = []
            while i < n:
                cur = lines[i].strip()
                match = _OL_RE.match(cur) if ordered else _UL_RE.match(cur)
                if not match:
                    break
                items.append(f'<li>{_render_inline(match.group(1))}</li>')
                i += 1
            out.append(f'<{tag}>{"".join(items)}</{tag}>')
            continue

        para: list[str] = [stripped]
        i += 1
        while i < n:
            nxt = lines[i].strip()
            if not nxt:
                break
            if (
                nxt.startswith('@@FENCE')
                or _HEADING_RE.match(nxt)
                or _HR_RE.match(nxt)
                or nxt.startswith('&gt;')
                or _UL_RE.match(nxt)
                or _OL_RE.match(nxt)
            ):
                break
            if '|' in nxt and i + 1 < n and _is_table_sep(lines[i + 1]):
                break
            para.append(nxt)
            i += 1
        out.append(f'<p>{_render_inline(" ".join(para))}</p>')

    return '\n'.join(out)
