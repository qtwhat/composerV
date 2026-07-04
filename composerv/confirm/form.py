"""Pure render + parse for the confirm review form. No IO — the server wires these to HTTP."""

from __future__ import annotations

from collections.abc import Callable
from html import escape

from pydantic import BaseModel

from composerv.faces.review import PersonRow
from composerv.store.db import Brief


class PersonUpdate(BaseModel):
    person_id: int
    name: str = ""
    sensitive: bool = False
    note: str = ""


class BriefInput(BaseModel):
    context: str = ""
    style: str = ""


_STYLE = ("<style>body{font-family:-apple-system,system-ui,sans-serif;margin:24px;max-width:900px}"
          ".p{display:flex;gap:12px;align-items:center;border:1px solid #ddd;border-radius:8px;"
          "padding:10px;margin:8px 0}.p img{width:96px;height:96px;object-fit:cover;border-radius:6px;"
          "background:#eee}label{display:block;font-size:12px;color:#666}input[type=text],textarea{"
          "width:100%;padding:6px;font-size:14px}textarea{height:70px}button{font-size:15px;"
          "padding:8px 18px;margin-top:12px}</style>")


def render_confirm_form(rows: list[PersonRow], brief: Brief | None, *,
                        crop_url: Callable[[int], str]) -> str:
    ctx = escape(brief.context) if brief else ""
    sty = escape(brief.style) if brief else ""
    parts = ['<!doctype html><html><head><meta charset="utf-8"><title>确认</title>',
             _STYLE, "</head><body><h1>确认人像与用户输入</h1>",
             '<form method="post" action="/save">']
    if rows:
        parts.append("<h2>人像（可跳过任意项）</h2>")
        for r in rows:
            checked = " checked" if r.sensitive else ""
            parts.append(
                f'<div class="p"><img src="{escape(crop_url(r.person_id))}">'
                f'<div style="flex:1">'
                f'<label>名字</label>'
                f'<input type="text" name="name_{r.person_id}" value="{escape(r.name)}">'
                f'<label>备注（角色/关系，可空）</label>'
                f'<input type="text" name="note_{r.person_id}" value="{escape(r.note)}">'
                f'<label><input type="checkbox" name="sensitive_{r.person_id}"{checked}> 敏感（不进自动剪辑）</label>'
                f'<div style="font-size:12px;color:#999">{r.n_clips} clips · {r.n_faces} faces</div>'
                f'</div></div>')
    else:
        parts.append("<p>未检测到人脸。只填下面的用户输入即可。</p>")
    parts.append(
        "<h2>用户输入（交给导演，最高优先级）</h2>"
        f'<label>整体上下文（什么场合 / 突出谁 / 避开什么）</label>'
        f'<textarea name="context">{ctx}</textarea>'
        f'<label>风格与节奏</label>'
        f'<textarea name="style">{sty}</textarea>'
        '<button type="submit">保存</button></form></body></html>')
    return "\n".join(parts)


def parse_confirm_submission(form: dict) -> tuple[list[PersonUpdate], BriefInput]:
    ids = sorted({int(k[len("name_"):]) for k in form if k.startswith("name_")})
    updates = [PersonUpdate(
        person_id=pid,
        name=str(form.get(f"name_{pid}", "")).strip(),
        sensitive=bool(form.get(f"sensitive_{pid}")),
        note=str(form.get(f"note_{pid}", "")).strip(),
    ) for pid in ids]
    brief = BriefInput(context=str(form.get("context", "")).strip(),
                       style=str(form.get("style", "")).strip())
    return updates, brief
