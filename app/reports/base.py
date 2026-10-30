"""Базовый рендер HTML-отчёта.

Тёмная тема: чёрный/графит/тёмно-серый фон, оранжевые и красные акценты.
Без внешних CDN и шрифтов, без тяжёлого JS. Всё, что нужно для длинных
отчётов (навигация по якорям, сворачивание, копирование, «наверх»),
включается только когда отчёт действительно длинный.

Безопасность: текст экранируется, ссылки проходят allow-list (http/https/
mailto), отчёт открывается с CSP, meta robots noindex/nofollow.
"""
from __future__ import annotations

import html
import re

CSS = """
:root{
  --bg:#0a0a0c;--bg2:#0e0e11;--card:#141418;--card2:#191920;
  --line:#26262e;--line2:#32323c;--txt:#ececf2;--mut:#9090a0;--dim:#6b6b7a;
  --acc:#ff7a1a;--acc-soft:rgba(255,122,26,.13);--acc-line:rgba(255,122,26,.35);
  --red:#ff4d4d;--red-soft:rgba(255,77,77,.12);--ok:#3fb950;--ok-soft:rgba(63,185,80,.12);
  --warn:#e3b341;--warn-soft:rgba(227,179,65,.12);
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace;
  --font:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif
}
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-text-size-adjust:100%}
body{
  background:var(--bg);color:var(--txt);font-family:var(--font);
  font-size:15px;line-height:1.58;padding:0 0 48px;
  overflow-x:hidden;word-break:break-word;overflow-wrap:anywhere
}
a{color:var(--acc);text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:820px;margin:0 auto;padding:0 14px}
.top{background:linear-gradient(180deg,#111114 0%,#0a0a0c 100%);
  border-bottom:1px solid var(--line);padding:22px 0 18px;margin-bottom:18px}
.brand{display:flex;align-items:center;gap:9px;font-size:12px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--dim);margin-bottom:10px}
.brand .mark{width:8px;height:8px;border-radius:2px;background:var(--acc);
  box-shadow:0 0 0 3px var(--acc-soft)}
h1{font-size:21px;line-height:1.25;font-weight:650;letter-spacing:-.01em}
.sub{color:var(--mut);font-size:13.5px;margin-top:6px}
.sub code{font-family:var(--mono);color:var(--txt);background:var(--card2);
  border:1px solid var(--line);border-radius:5px;padding:1px 6px;font-size:12.5px}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
.tag{font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:var(--mut);
  background:var(--card);border:1px solid var(--line);border-radius:999px;padding:3px 9px}
.tag.acc{color:var(--acc);border-color:var(--acc-line);background:var(--acc-soft)}
.tag.red{color:var(--red);border-color:rgba(255,77,77,.35);background:var(--red-soft)}
.tag.ok{color:var(--ok);border-color:rgba(63,185,80,.35);background:var(--ok-soft)}
.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(104px,1fr));gap:8px;margin-bottom:16px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:11px 13px;min-width:0}
.stat .n{font-size:20px;font-weight:700;color:var(--acc);line-height:1.2;
  overflow-wrap:anywhere}
.stat .l{color:var(--mut);font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;margin-top:3px}
.nav{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:10px;margin-bottom:14px;display:flex;flex-wrap:wrap;gap:6px}
.nav a{font-size:12.5px;color:var(--mut);background:var(--bg2);border:1px solid var(--line2);
  border-radius:7px;padding:5px 10px}
.nav a:hover{color:var(--acc);border-color:var(--acc-line);text-decoration:none}
.section{background:var(--card);border:1px solid var(--line);border-radius:11px;
  margin-bottom:11px;overflow:hidden;scroll-margin-top:12px}
.section-title{display:flex;align-items:center;gap:8px;font-size:11.5px;font-weight:650;
  color:var(--mut);padding:11px 14px;border-bottom:1px solid var(--line);
  text-transform:uppercase;letter-spacing:.07em}
.section-title::before{content:"";width:3px;height:12px;border-radius:2px;
  background:var(--acc);flex-shrink:0}
.section-title .count{color:var(--dim);font-weight:500;letter-spacing:0}
.card-title{font-size:12px;font-weight:600;color:var(--txt);padding:10px 14px 6px;
  letter-spacing:.02em;border-bottom:1px solid var(--line);background:var(--bg2)}
.row{display:flex;gap:12px;padding:9px 14px;border-bottom:1px solid var(--line);font-size:14px}
.row:last-child{border-bottom:none}
.row:hover{background:rgba(255,255,255,.015)}
.label{min-width:126px;max-width:48%;color:var(--mut);font-size:12.5px;
  flex-shrink:0;overflow-wrap:anywhere}
.value{flex:1;min-width:0;overflow-wrap:anywhere}
.value.mono{font-family:var(--mono);font-size:12.8px;color:var(--acc)}
.value.danger{color:var(--red)}
.value.ok{color:var(--ok)}
.value.warn{color:var(--warn)}
.value a{overflow-wrap:anywhere}
.copy{flex-shrink:0;background:transparent;border:1px solid var(--line2);color:var(--dim);
  font-family:var(--font);font-size:10.5px;letter-spacing:.04em;text-transform:uppercase;
  border-radius:6px;padding:2px 7px;cursor:pointer;align-self:flex-start;margin-left:2px}
.copy:hover{color:var(--acc);border-color:var(--acc-line)}
.copy.done{color:var(--ok);border-color:rgba(63,185,80,.4)}
.note{padding:11px 14px;color:var(--mut);font-size:13px;border-bottom:1px solid var(--line)}
.note:last-child{border-bottom:none}
.note.danger{color:var(--red)}
.note a,.note code{overflow-wrap:anywhere}
.empty{padding:13px 14px;color:var(--dim);font-size:13px;font-style:italic}
.list a{display:block;padding:10px 14px;border-bottom:1px solid var(--line);
  color:var(--acc);font-size:13.5px}
.list a:last-child{border-bottom:none}
.list a:hover{background:rgba(255,122,26,.05);text-decoration:none}
.list .domain{display:block;color:var(--dim);font-size:11px;margin-top:3px;
  font-family:var(--mono);overflow-wrap:anywhere}
.gift{display:flex;gap:10px;padding:9px 14px;border-bottom:1px solid var(--line);font-size:13.5px}
.gift:last-child{border-bottom:none}
.gift .g{flex:1;min-width:0}
.gift .m{color:var(--mut);font-size:12px;font-style:italic;margin-top:2px}
.gift .d{color:var(--dim);font-size:11px;margin-top:2px;font-family:var(--mono)}
.badge{padding:2px 7px;border-radius:5px;font-size:10.5px;font-weight:700;
  height:fit-content;white-space:nowrap;letter-spacing:.03em}
.badge.nft{background:var(--acc-soft);color:var(--acc)}
.badge.stars{background:var(--warn-soft);color:var(--warn)}
.badge.gift{background:var(--red-soft);color:var(--red)}
.result-card{padding:13px 14px;border-bottom:1px solid var(--line)}
.result-card:last-child{border-bottom:none}
.result-title{display:block;color:var(--acc);font-size:14px;font-weight:600;
  line-height:1.4;overflow-wrap:anywhere}
.result-title:hover{text-decoration:underline}
.result-domain{color:var(--dim);font-size:11.5px;margin-top:4px;
  font-family:var(--mono);overflow-wrap:anywhere}
.result-snippet{color:#c3c3d0;font-size:13px;margin-top:6px;line-height:1.5;overflow-wrap:anywhere}
.chips{display:flex;flex-wrap:wrap;gap:7px;padding:11px 14px}
.chip{background:var(--bg2);border:1px solid var(--line2);border-radius:999px;
  padding:4px 10px;font-size:12px;color:var(--txt);overflow-wrap:anywhere;max-width:100%}
details{border:0}
details>summary{list-style:none;cursor:pointer;padding:9px 14px;color:var(--acc);
  font-size:12.5px;border-top:1px solid var(--line);user-select:none}
details>summary::-webkit-details-marker{display:none}
details>summary::before{content:"+ ";color:var(--dim)}
details[open]>summary::before{content:"− "}
details>summary:hover{background:rgba(255,122,26,.05)}
.src{padding:10px 14px;border-top:1px solid var(--line);color:var(--dim);font-size:11.5px}
.src a{color:var(--mut)}
.footer{text-align:center;color:var(--dim);font-size:11.5px;margin-top:22px;
  padding-top:14px;border-top:1px solid var(--line);line-height:1.7}
.footer a{color:var(--mut)}
.totop{position:fixed;right:14px;bottom:14px;z-index:5;background:var(--card2);
  border:1px solid var(--line2);color:var(--mut);width:38px;height:38px;
  border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-size:16px;box-shadow:0 4px 14px rgba(0,0,0,.4)}
.totop:hover{color:var(--acc);border-color:var(--acc-line);text-decoration:none}
@media (max-width:420px){
  body{font-size:14.5px;padding-bottom:44px}
  .wrap{padding:0 10px}
  h1{font-size:19px}
  .row{flex-direction:column;gap:3px;padding:9px 12px}
  .label{min-width:0;max-width:100%;font-size:11.5px;text-transform:uppercase;letter-spacing:.04em}
  .copy{margin-left:0;margin-top:4px;align-self:flex-start}
  .stat{padding:9px 11px}
  .stat .n{font-size:18px}
  .stats{grid-template-columns:repeat(auto-fill,minmax(88px,1fr))}
}
@media (prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
@media print{
  body{background:#fff;color:#111}
  .nav,.totop,.copy{display:none}
  .section,.stat{border-color:#ccc;background:#fff}
}
"""

CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:; "
    "script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
)

SHELL = """<!DOCTYPE html><html lang="{lang}"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{csp}">
<meta name="robots" content="noindex, nofollow, noarchive">
<meta name="referrer" content="no-referrer">
<meta name="color-scheme" content="dark">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{title}</title><style>{css}</style></head><body id="top">
<div class="top"><div class="wrap"><div class="brand"><span class="mark"></span>{brand}</div>
<h1>{title}</h1>{subtitle}{tags}</div></div>
<div class="wrap">{stats}{nav}{body}
{attribution}<div class="footer">{footer}</div></div>{totop}{script}
</body></html>"""

_HREF_RE = re.compile(r'href="([^"]*)"')
_SAFE_HREF = ("http://", "https://", "mailto:")

# Минимальный JS: копирование значений. Без него страница полностью рабочая.
SCRIPT = """<script>
(function(){
  function copyText(t,btn){
    var base=btn.getAttribute('data-label')||btn.textContent;
    function ok(){btn.textContent=btn.getAttribute('data-done')||'ok';btn.classList.add('done');
      setTimeout(function(){btn.textContent=base;btn.classList.remove('done');},1200);}
    if(navigator.clipboard&&navigator.clipboard.writeText){
      navigator.clipboard.writeText(t).then(ok,function(){});
    }else{
      var a=document.createElement('textarea');a.value=t;a.style.position='fixed';
      a.style.opacity='0';document.body.appendChild(a);a.select();
      try{document.execCommand('copy');ok();}catch(e){}document.body.removeChild(a);
    }
  }
  document.addEventListener('click',function(e){
    var b=e.target&&e.target.closest?e.target.closest('.copy'):null;
    if(!b)return;
    e.preventDefault();
    copyText(b.getAttribute('data-copy')||'',b);
  });
})();
</script>"""


def _safe_href(value: str) -> str:
    decoded = html.unescape(value or "").strip()
    if decoded.lower().startswith(_SAFE_HREF):
        return value
    return ""


def _sanitize_links(html_text: str) -> str:
    """Оставляет только http/https/mailto, добавляет rel к target=_blank."""
    def repl(match: re.Match) -> str:
        return f'href="{_safe_href(match.group(1))}"'

    html_text = _HREF_RE.sub(repl, html_text)
    html_text = html_text.replace(
        'target="_blank"', 'target="_blank" rel="noopener noreferrer"'
    )
    return html_text


def _normalize_id(value: str) -> str:
    value = re.sub(r"[^a-z0-9-]+", "-", (value or "").lower())
    return value.strip("-") or "sec"


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


COPY_LABEL = {"ru": "копировать", "en": "copy"}
COPY_DONE = {"ru": "готово", "en": "ok"}


def _render_cell(row, language: str = "ru") -> str:
    label, value = row[0], row[1]
    mono = bool(row[2]) if len(row) > 2 else False
    cls = row[3] if len(row) > 3 and row[3] else ("value mono" if mono else "value")
    copy_btn = ""
    if mono and str(value or "").strip():
        copy_btn = (
            f'<button type="button" class="copy" data-copy="{esc(value)}" '
            f'data-done="{esc(COPY_DONE.get(language, "ok"))}" '
            f'aria-label="{esc(COPY_LABEL.get(language, "copy"))}">'
            f'{esc(COPY_LABEL.get(language, "copy"))}</button>'
        )
    return (
        f'<div class="row"><div class="label">{esc(label)}</div>'
        f'<div class="{cls}">{esc(value)}</div>{copy_btn}</div>'
    )


def _rows_html(rows: list, language: str = "ru") -> str:
    return "".join(_render_cell(row, language) for row in rows)


def section_html(section: dict, language: str = "ru") -> str:
    sid = _normalize_id(section.get("id") or "sec")
    title = esc(section.get("title") or "")
    count = section.get("count")
    count_html = f'<span class="count">· {esc(count)}</span>' if count else ""
    body = ""
    if section.get("note"):
        note_cls = "note danger" if section.get("tone") == "danger" else "note"
        body += f'<div class="{note_cls}">{esc(section["note"])}</div>'
    if section.get("rows"):
        rows = section["rows"]
        # Длинные блоки сворачиваются автоматически, чтобы не растягивать
        # страницу; короткие остаются раскрытыми.
        if section.get("collapsed") or len(rows) > 18:
            visible = 12 if section.get("collapsed") else 18
            shown, rest = rows[:visible], rows[visible:]
            body += _rows_html(shown, language)
            more = section.get("more_label") or (
                "Показать все" if language == "ru" else "Show all"
            )
            body += (
                f"<details><summary>{esc(more)} ({len(rows)})</summary>"
                f"{_rows_html(rest, language)}</details>"
            )
        else:
            body += _rows_html(rows, language)
    for card in section.get("cards", []):
        if card.get("title"):
            body += f'<div class="card-title">{esc(card["title"])}</div>'
        body += _rows_html(card.get("rows", []), language)
        if card.get("html"):
            body += card["html"]
    if section.get("html"):
        body += section["html"]
    if section.get("source"):
        body += f'<div class="src">{section["source"]}</div>'
    if not body:
        body = f'<div class="empty">{esc(section.get("empty") or "—")}</div>'
    return f'<div class="section" id="{sid}"><div class="section-title">{title}{count_html}</div>{body}</div>'


def _nav_html(sections: list[dict], language: str = "ru") -> str:
    items = []
    for section in sections:
        title = section.get("title")
        if not title:
            continue
        sid = _normalize_id(section.get("id") or "sec")
        items.append(f'<a href="#{sid}">{esc(title)}</a>')
    if len(items) < 4:
        return ""
    return '<nav class="nav">' + "".join(items) + "</nav>"


def render_report(
    *,
    title: str,
    kind: str = "",
    subtitle: str = "",
    source: str = "",
    language: str = "ru",
    sections: list[dict] | None = None,
    stats: list[tuple] | None = None,
    footer: str = "",
    tags: list[str] | None = None,
    attribution: str = "",
    brand: str = "INFEX",
    **extras,
) -> bytes:
    """Собирает HTML-отчёт.

    `kind`, `source`, а также унаследованные display_name/display_handle/
    avatar_text принимаются для совместимости и не выводятся отдельной шапкой.
    """
    from app.reports.locales import rl

    sections = sections or []
    footer = footer or rl(language, "footer")
    body = "".join(section_html(s, language) for s in sections)

    stats_html = ""
    if stats:
        stats_html = '<div class="stats">' + "".join(
            f'<div class="stat"><div class="n">{esc(n)}</div><div class="l">{esc(l)}</div></div>'
            for n, l in stats
        ) + "</div>"

    tag_items = list(tags or [])
    if source and source not in tag_items:
        tag_items.append(source)
    tags_html = ""
    if tag_items:
        tags_html = '<div class="tags">' + "".join(
            f'<span class="tag">{esc(item)}</span>' for item in tag_items
        ) + "</div>"

    attribution_html = ""
    if attribution:
        attribution_html = (
            '<div class="src" style="border:0;text-align:center;margin-top:16px">'
            + _sanitize_links(attribution)
            + "</div>"
        )
    sub_html = f'<div class="sub">{esc(subtitle)}</div>' if subtitle else ""
    body = _sanitize_links(body)
    stats_html = _sanitize_links(stats_html)
    nav_html = _nav_html(sections, language)
    totop = '<a class="totop" href="#top" aria-label="top">↑</a>' if len(sections) >= 4 else ""
    script = SCRIPT if any(s.get("rows") for s in sections) else ""

    rendered = SHELL.format(
        lang=esc(language),
        csp=CSP,
        title=esc(title),
        brand=esc(brand),
        subtitle=sub_html,
        tags=tags_html,
        css=CSS,
        stats=stats_html,
        nav=nav_html,
        body=body,
        footer=esc(footer),
        attribution=attribution_html,
        totop=totop,
        script=script,
    )
    return rendered.encode("utf-8")


__all__ = [
    "CSS",
    "CSP",
    "esc",
    "render_report",
    "section_html",
]
