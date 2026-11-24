"""Render the memory database as one self-contained HTML document.

The page carries its own styles and script and loads nothing from the network,
so it can be attached to a pull request, opened from a file share, or kept as
an artifact without a running service.
"""

import html
import json
from collections.abc import Mapping
from typing import Literal

from pydantic import JsonValue

from agent_qa.storage import MemoryRecord

SectionKind = Literal["fixtures", "api_contracts", "failures", "flaky_tests"]

SECTIONS: tuple[tuple[SectionKind, str], ...] = (
    ("fixtures", "Fixtures"),
    ("api_contracts", "API contracts"),
    ("failures", "Failures"),
    ("flaky_tests", "Flaky tests"),
)

_HIGHLIGHT_FIELDS = (
    "confidence",
    "times_applied",
    "times_worked",
    "root_cause",
    "fix",
    "test_name",
    "name",
    "endpoint",
    "method",
    "status_code",
)

_SCRIPT = """
const box = document.getElementById('filter');
const cards = [...document.querySelectorAll('.card')];
const sections = [...document.querySelectorAll('section')];
box.addEventListener('input', () => {
  const needle = box.value.trim().toLowerCase();
  for (const card of cards) {
    card.hidden = needle !== '' && !card.textContent.toLowerCase().includes(needle);
  }
  for (const section of sections) {
    const shown = section.querySelectorAll('.card:not([hidden])').length;
    section.hidden = needle !== '' && shown === 0;
  }
});
""".strip()

_STYLE = """
*{box-sizing:border-box}
body{margin:0;background:#f6f7f9;color:#1c1f23;
  font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
header{position:sticky;top:0;z-index:2;background:#101418;color:#f6f7f9;
  padding:14px 20px;display:flex;gap:14px;align-items:baseline;flex-wrap:wrap}
header h1{margin:0;font-size:16px;letter-spacing:.02em}
header span{color:#9aa4af;font-size:13px}
main{padding:20px;max-width:1080px;margin:0 auto;display:grid;gap:20px}
section{background:#fff;border:1px solid #dfe3e8;border-radius:8px;overflow:hidden}
h2{margin:0;font-size:13px;text-transform:uppercase;letter-spacing:.08em;
  padding:12px 16px;border-bottom:1px solid #e7eaee;display:flex;
  justify-content:space-between;color:#3b4249}
h2 em{font-style:normal;color:#8b949e}
input{width:100%;padding:10px 16px;border:0;border-bottom:1px solid #e7eaee;
  font:inherit;outline:none}
input:focus{background:#fbfcfd}
.card{padding:12px 16px;border-bottom:1px solid #eef1f4}
.card:last-child{border-bottom:0}
.card[hidden]{display:none}
.title{font-weight:600;word-break:break-word}
dl{margin:8px 0 0;display:grid;grid-template-columns:auto 1fr;gap:2px 12px}
dt{color:#79828b;font-size:12px}
dd{margin:0;word-break:break-word;white-space:pre-wrap}
.meter{margin-top:8px;height:5px;background:#e7eaee;border-radius:3px;overflow:hidden}
.meter i{display:block;height:100%;background:#2f9e63}
.pill{margin-left:8px;padding:1px 7px;border-radius:10px;font-size:11px;
  background:#e7eaee;color:#3b4249}
.pill.warn{background:#fbe3e3;color:#93262b}
.empty{padding:12px 16px;color:#79828b}
pre{margin:8px 0 0;padding:8px;background:#0f1317;color:#dfe6ed;border-radius:5px;
  overflow-x:auto;font-size:12px}
""".strip()


def render_html(snapshot: Mapping[str, list[MemoryRecord]]) -> str:
    """Return one HTML document holding every section of a memory snapshot."""
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>Agent QA memory</title>",
        f"<style>{_STYLE}</style>",
        "</head><body>",
        "<header><h1>Agent QA memory</h1>",
        f"<span>{_totals(snapshot)}</span></header>",
        "<main>",
        '<input id="filter" type="search" placeholder="Filter by any text on the page"',
        ' aria-label="Filter">',
    ]
    for kind, title in SECTIONS:
        parts.append(_section(kind, title, snapshot.get(kind, [])))
    parts.append("</main>")
    parts.append(f"<script>{_SCRIPT}</script>")
    parts.append("</body></html>")
    return "\n".join(parts)


def _totals(snapshot: Mapping[str, list[MemoryRecord]]) -> str:
    counted = " · ".join(
        f"{len(snapshot.get(kind, []))} {title.lower()}" for kind, title in SECTIONS
    )
    return f"{counted} · generated locally, no network requests"


def _section(kind: SectionKind, title: str, records: list[MemoryRecord]) -> str:
    body = "".join(_card(kind, record) for record in records)
    if not body:
        body = '<p class="empty">Nothing recorded.</p>'
    return (
        f'<section id="{kind}"><h2>{html.escape(title)}'
        f"<em>{len(records)}</em></h2>{body}</section>"
    )


def _card(kind: SectionKind, record: MemoryRecord) -> str:
    rows = "".join(
        f"<dt>{html.escape(field.replace('_', ' '))}</dt><dd>{_value(record[field])}</dd>"
        for field in _HIGHLIGHT_FIELDS
        if record.get(field) not in (None, "")
    )
    return (
        f'<article class="card" data-section="{kind}">'
        f'<div class="title">{_title(kind, record)}</div>'
        f"<dl>{rows}</dl>"
        f"{_confidence_meter(record)}"
        f"<details><summary>All fields</summary><pre>{_value(record)}</pre></details>"
        "</article>"
    )


def _title(kind: SectionKind, record: MemoryRecord) -> str:
    if kind == "failures":
        pill = '<span class="pill">resolved</span>' if record.get("resolved") else ""
        return f"{_value(record.get('test_name'))}{pill}"
    if kind == "flaky_tests":
        pill = ""
        if record.get("consistently_failing") is True:
            pill = '<span class="pill warn">consistently failing</span>'
        elif record.get("quarantined") is True:
            pill = '<span class="pill">quarantined</span>'
        name = _value(record.get("test_name"))
        framework = _value(record.get("framework"))
        score = _value(record.get("flaky_score"))
        return f"{name} <em>· {framework} · score {score}</em>{pill}"
    if kind == "api_contracts":
        method = _value(record.get("method"))
        endpoint = _value(record.get("endpoint"))
        code = _value(record.get("status_code"))
        return f"{method} {endpoint} <em>· {code}</em>"
    return _value(record.get("name"))


def _confidence_meter(record: MemoryRecord) -> str:
    confidence = record.get("confidence")
    if not isinstance(confidence, int | float) or isinstance(confidence, bool):
        return ""
    percent = max(0.0, min(float(confidence), 1.0)) * 100.0
    return (
        '<div class="meter" title="confidence">'
        f'<i style="width:{percent:.0f}%"></i></div>'
    )


def _value(value: JsonValue) -> str:
    """Render one stored value as escaped display text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return html.escape(value)
    return html.escape(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
