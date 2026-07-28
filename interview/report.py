"""Downloadable interview report.

Print-ready HTML: it opens in a browser and prints to PDF via the browser's own
engine, which avoids pulling a heavyweight PDF library into a deployment that
has to fit in 512 MB.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any, Dict, List

from interview.scoring import RUBRIC_KEYS, RUBRIC_LABELS
from interview.ui.theme import verdict_for


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _profile_lines(profile: Dict[str, Any]) -> List[tuple[str, str]]:
    return [
        ("Field", profile.get("job_field", "—")),
        ("Level", profile.get("experience_level", "—")),
        ("Company size", profile.get("company_size", "—")),
        ("Style", profile.get("interview_style", "—")),
        ("Interviewer", profile.get("personality", "—")),
    ]


_PRINT_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  max-width: 800px; margin: 2.5rem auto; padding: 0 1.5rem; color: #1c1f2b; line-height: 1.6; }
h1 { font-size: 1.9rem; margin-bottom: .2rem; letter-spacing: -.02em; }
h2 { font-size: 1.25rem; margin-top: 2.2rem; padding-bottom: .3rem;
  border-bottom: 1px solid #e3e6ef; letter-spacing: -.01em; }
h3 { font-size: 1.02rem; margin-top: 1.5rem; }
.stamp { color: #6b7280; font-size: .85rem; margin-bottom: 1.8rem; }
.hero { display: flex; align-items: center; gap: 1.4rem; padding: 1.3rem 1.5rem;
  border-radius: 14px; background: #f4f6fc; border: 1px solid #e3e6ef; margin-bottom: 1.5rem; }
.hero .n { font-size: 3rem; font-weight: 800; line-height: 1; }
.hero .v { font-weight: 650; margin-bottom: .2rem; }
table { border-collapse: collapse; width: 100%; margin: .6rem 0 1rem; font-size: .93rem; }
th, td { border: 1px solid #e3e6ef; padding: .45rem .7rem; text-align: left; }
th { background: #f4f6fc; font-weight: 650; }
blockquote { margin: .6rem 0; padding: .55rem .95rem; border-left: 3px solid #4f7cff;
  background: #f7f9ff; color: #414759; font-style: italic; }
ul { padding-left: 1.3rem; }
li { margin: .28rem 0; }
.bar { height: .5rem; background: #e3e6ef; border-radius: 999px; overflow: hidden; min-width: 90px; }
.bar > i { display: block; height: 100%; background: #4f7cff; }
footer { margin-top: 2.5rem; padding-top: 1rem; border-top: 1px solid #e3e6ef;
  color: #6b7280; font-size: .82rem; }
@media print { body { margin: 0; max-width: none; } h2 { page-break-after: avoid; } }
"""


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def build_printable_html(
    result: Dict[str, Any], profile: Dict[str, Any], qa_history: List[Dict[str, Any]]
) -> str:
    """Render a self-contained HTML report; print to PDF from the browser."""
    score = result.get("overall_score", 0)
    verdict, colour = verdict_for(score)
    rubric = result.get("rubric", {})

    rows = "".join(
        f"<tr><td>{_esc(RUBRIC_LABELS[key])}</td>"
        f"<td><div class='bar'><i style='width:{float(rubric.get(key, 0)) * 10:.0f}%'></i></div></td>"
        f"<td>{_esc(rubric.get(key, 0))}/10</td></tr>"
        for key in RUBRIC_KEYS
    )

    profile_rows = "".join(
        f"<tr><th>{_esc(label)}</th><td>{_esc(value)}</td></tr>"
        for label, value in _profile_lines(profile)
    )

    def bullet_list(items: List[str]) -> str:
        return "".join(f"<li>{_esc(item)}</li>" for item in items)

    strengths = result.get("strengths") or []
    improvements = result.get("improvements") or []

    sections = [
        f"""<div class="hero">
              <div class="n" style="color:{colour}">{_esc(score)}<span style="font-size:.32em;opacity:.55">/100</span></div>
              <div><div class="v" style="color:{colour}">{_esc(verdict)}</div>
                   <div>{_esc(result.get('summary', ''))}</div></div>
            </div>""",
        f"<h2>Interview setup</h2><table>{profile_rows}</table>",
        f"<h2>Rubric</h2><table><tr><th>Criterion</th><th></th><th>Score</th></tr>{rows}</table>",
    ]

    if strengths:
        sections.append(f"<h2>Strengths</h2><ul>{bullet_list(strengths)}</ul>")
    if improvements:
        sections.append(f"<h2>Areas to improve</h2><ul>{bullet_list(improvements)}</ul>")

    notes = result.get("question_notes") or []
    if notes:
        blocks = []
        for index, note in enumerate(notes, start=1):
            block = [f"<h3>Q{index}. {_esc(note.get('question', ''))}</h3>"]
            if (note.get("answer_excerpt") or "").strip():
                block.append(f"<blockquote>{_esc(note['answer_excerpt'])}</blockquote>")
            if (note.get("diagnosis") or "").strip():
                block.append(f"<p>{_esc(note['diagnosis'])}</p>")
            if note.get("fixes"):
                block.append(f"<p><b>How to improve:</b></p><ul>{bullet_list(note['fixes'])}</ul>")
            blocks.append("".join(block))
        sections.append("<h2>Question-by-question</h2>" + "".join(blocks))

    if qa_history:
        transcript = "".join(
            f"<h3>Q{index}. {_esc(item.get('q', ''))}</h3>"
            f"<p>{_esc(item.get('a', '')) or '<i>(no answer captured)</i>'}</p>"
            for index, item in enumerate(qa_history, start=1)
        )
        sections.append(f"<h2>Full transcript</h2>{transcript}")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>InteReviewAI — Interview Report</title>
<style>{_PRINT_CSS}</style></head>
<body>
<h1>InteReviewAI — Interview Report</h1>
<div class="stamp">Generated {_esc(_timestamp())} &middot; {_esc(profile.get('job_field', ''))}</div>
{''.join(sections)}
<footer>Practice report from InteReviewAI. Scores are directional feedback from a
language model, not a hiring decision. Tip: use your browser's Print dialog and choose
&ldquo;Save as PDF&rdquo;.</footer>
</body></html>"""
