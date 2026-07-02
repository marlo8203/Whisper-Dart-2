"""Pure-function helpers for rendering the sidebar table and other UI bits."""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from ui.constants import INBOX_ID


def entry_title(entry: dict) -> str:
    t = entry.get("title")
    if t and t.strip():
        return t.strip()
    name = entry.get("audio_name", "")
    return Path(name).stem or "Untitled"


def project_name(pid: str | None, projects: list[dict]) -> str:
    if not pid or pid == INBOX_ID:
        return "Inbox"
    for p in projects:
        if p["id"] == pid:
            return p["name"]
    return "Inbox"


def project_dropdown_choices(projects: list[dict]) -> list[tuple[str, str]]:
    out = [("📥 Inbox", INBOX_ID)]
    for p in projects:
        out.append((f"📁 {p['name']}", p["id"]))
    return out


def progress_html(pct: int) -> str:
    pct = max(0, min(100, int(pct)))
    return (
        f'<div class="pbar-wrap">'
        f'<div class="pbar"><div class="pbar-fill" style="width:{pct}%"></div></div>'
        f'<span class="pbar-pct">{pct}%</span>'
        f'</div>'
    )


# ── Custom meetings list (replaces the Dataframe) ───────────────────────────────

def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def _chip(tag: dict) -> str:
    color = _esc(tag.get("color", "#5B4FE6"))
    name = _esc(tag.get("name", ""))
    return f'<span class="mt-chip" style="--tag-color:{color}"><span class="mt-chip-dot"></span>{name}</span>'


def _epoch(ts: str) -> int:
    try:
        return int(datetime.fromisoformat(ts).timestamp())
    except (ValueError, TypeError):
        return 0


def meetings_html(history, projects, running_id=None, selected_id=None) -> str:
    """Render the meetings list as self-contained HTML.

    Rows carry data-* attributes (title / project / date / tags / search) so the
    client can filter and sort without a server round-trip. `data-alltags` on the
    container feeds the per-row Tags submenu built in JS.
    """
    all_tags = []
    seen = set()
    for entry in history:
        for t in entry.get("tags", []):
            if t["id"] not in seen:
                seen.add(t["id"])
                all_tags.append({"id": t["id"], "name": t["name"], "color": t.get("color", "#5B4FE6")})
    alltags_attr = _esc(json.dumps(all_tags, ensure_ascii=False))

    rows = []
    for entry in history:
        eid = entry["id"]
        title = entry_title(entry) or "Untitled"
        pname = project_name(entry.get("project_id"), projects)
        is_draft = not (entry.get("text") or "").strip()
        is_running = bool(running_id) and eid == running_id
        tags = entry.get("tags", [])
        try:
            dt = datetime.fromisoformat(entry["timestamp"])
            date_short = dt.strftime("%d.%m.%y %H:%M")
        except (ValueError, KeyError):
            date_short = "—"

        chips = "".join(_chip(t) for t in tags)
        tag_names = " ".join(t.get("name", "") for t in tags)
        search_blob = _esc(f"{title} {pname} {tag_names}".lower())
        tags_json = _esc(json.dumps(
            [{"id": t["id"], "name": t["name"], "color": t.get("color", "#5B4FE6")} for t in tags],
            ensure_ascii=False,
        ))
        classes = "mt-row"
        if is_running:
            classes += " mt-running"
        if selected_id and eid == selected_id:
            classes += " mt-selected"
        marker = '<span class="mt-run-dot"></span>' if is_running else (
            '<span class="mt-draft-dot">⊕</span>' if is_draft else "")

        rows.append(
            f'<div class="{classes}" data-id="{_esc(eid)}" '
            f'data-title="{_esc(title.lower())}" data-project="{_esc(pname.lower())}" '
            f'data-date="{_epoch(entry.get("timestamp", ""))}" '
            f'data-search="{search_blob}" data-tags=\'{tags_json}\'>'
            f'<div class="mt-body">'
            f'<div class="mt-title-row">{marker}<span class="mt-title">{_esc(title)}</span></div>'
            f'<div class="mt-meta"><span class="mt-project">{_esc(pname)}</span>'
            f'<span class="mt-dot">·</span><span class="mt-date">{_esc(date_short)}</span></div>'
            f'<div class="mt-chips">{chips}</div>'
            f'</div>'
            f'<button class="mt-kebab" data-id="{_esc(eid)}" title="Actions" '
            f'aria-label="Actions">⋯</button>'
            f'</div>'
        )

    empty = '<div class="mt-empty">No transcripts yet.</div>' if not rows else ""

    return (
        f'<div id="meetings" data-alltags=\'{alltags_attr}\' '
        f'data-selected="{_esc(selected_id or "")}">'
        f'<div class="mt-toolbar">'
        f'<span class="mt-sort-label">Sort by</span>'
        f'<div class="mt-sort" role="group" aria-label="Sort">'
        f'<button class="mt-sort-btn active" data-sort="date">Date</button>'
        f'<button class="mt-sort-btn" data-sort="title">Name</button>'
        f'<button class="mt-sort-btn" data-sort="project">Project</button>'
        f'<button class="mt-sort-dir" data-dir="desc" title="Toggle direction">↓</button>'
        f'</div></div>'
        f'<div class="mt-list">{"".join(rows)}{empty}</div>'
        f'</div>'
    )
