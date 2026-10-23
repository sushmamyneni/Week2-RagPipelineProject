# Stage 7: build the hand-check report (data/report.html) from data/run_results.json.
# No model calls, so it is free and safe to rerun.
#
# What this file does when finished:
#   Step 2: Load the results and compute the summary numbers
#   Step 3: Build the page skeleton (HTML and CSS) with the summary at the top
#   Step 4: One card per question: answer or refusal, sources, issues, retrieved chunks
#   Step 5: Hand-check controls (verdict and notes), saved in the browser
#   Step 6: Write data/report.html
#
# Usage:
#   python 7_build_report.py
import html
import json
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Step 2: Load the results and compute the summary
# ---------------------------------------------------------------------------
cfg = yaml.safe_load(open("config.yaml"))
data_dir = Path(cfg["paths"]["data_dir"])

# The file written by 6b_run_questions.py: one dict per question
results = json.load(open(data_dir / "run_results.json", encoding="utf-8"))

# A pointer for the hand check, not a score: an unanswerable question (empty "expect")
# should be refused, and any other question should be answered.
# Returns "", "WRONG REFUSAL" or "SHOULD HAVE REFUSED".
def flag(r):
    if r["error"]:
        return "ERROR"
    should_refuse = not r["expect"]
    if r["refused"] == should_refuse:
        return ""
    return "WRONG REFUSAL" if r["refused"] else "SHOULD HAVE REFUSED"

ok = [r for r in results if not r["error"]]
answered = [r for r in ok if not r["refused"]]
refused = [r for r in ok if r["refused"]]

summary = {
    "questions": len(results),
    "answered": len(answered),
    "refused": len(refused),
    "refused by threshold": sum(1 for r in refused if r["refused_by"] == "threshold"),
    "refused by model": sum(1 for r in refused if r["refused_by"] == "model"),
    "errors": len(results) - len(ok),
    "flagged": [r["question"] for r in results if flag(r)],
    "answers with citation issues": sum(1 for r in answered if r["citation_issues"]),
    # Model calls only happen for answered questions and model refusals
    "avg latency, answered (s)": round(sum(r["latency_s"] for r in answered) / len(answered), 1) if answered else None,
    "avg latency, refused (s)": round(sum(r["latency_s"] for r in refused) / len(refused), 1) if refused else None,
}

# Print it for now, so it can be checked before any HTML exists
for key, value in summary.items():
    print(f"{key}: {value}")

# ---------------------------------------------------------------------------
# Step 3: The page skeleton and the summary box
# ---------------------------------------------------------------------------
from datetime import datetime

# Everything that comes from the data (questions, answers, chunk text) goes through esc()
# before it is put in the page. Chunk text contains characters like < and & that would
# otherwise be read as HTML.
def esc(value):
    return html.escape(str(value))

CSS = """
body { font-family: system-ui, sans-serif; max-width: 1000px; margin: 2rem auto; padding: 0 1rem; color: #1f2933; background: #ffffff; line-height: 1.5; }
h1 { margin-bottom: 0.2rem; }
.muted { color: #52606d; font-size: 0.9rem; }
.summary { border: 1px solid #d9dee3; border-radius: 8px; padding: 1rem 1.25rem; background: #f7f9fb; }
.summary table { border-collapse: collapse; }
.summary td { padding: 0.15rem 1.5rem 0.15rem 0; }
.summary td:first-child { color: #52606d; }
.flagged li { color: #b00020; }
.card { border: 1px solid #d9dee3; border-radius: 8px; padding: 1rem 1.25rem; margin: 1.25rem 0; }
.card h2 { font-size: 1.05rem; margin: 0 0 0.5rem; }
.tag { display: inline-block; font-size: 0.8rem; padding: 0.05rem 0.55rem; border-radius: 999px; margin-right: 0.35rem; }
.answered { background: #d4edda; }
.refused { background: #fff3cd; }
.flag { background: #f8d7da; color: #7a1020; font-weight: 600; }
.answer { white-space: pre-wrap; border-left: 4px solid #d9dee3; padding-left: 0.75rem; margin: 0.75rem 0; }
.issues { color: #b00020; }
details { margin: 0.4rem 0; }
pre { white-space: pre-wrap; background: #f0f3f6; padding: 0.6rem; border-radius: 6px; font-size: 0.85rem; }
.verdict { margin-top: 0.75rem; padding-top: 0.6rem; border-top: 1px dashed #d9dee3; }
.verdict label { margin-right: 1rem; }
.verdict textarea { width: 100%; box-sizing: border-box; margin-top: 0.4rem; }
"""

# The summary box at the top of the page
def summary_block():
    rows = "".join(f"<tr><td>{esc(k)}</td><td><strong>{esc(v)}</strong></td></tr>"
                   for k, v in summary.items() if k != "flagged")
    flagged = "".join(f"<li>{esc(q)}</li>" for q in summary["flagged"]) or "<li style='color:#1f2933'>none</li>"
    return (f'<div class="summary"><table>{rows}</table>'
            f'<p><strong>Flagged for your attention</strong> (a refusal or an answer that goes against what was expected):</p>'
            f'<ul class="flagged">{flagged}</ul></div>')

# The whole page around the cards
def build_page(cards):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RAG answer report</title>
<style>{CSS}</style>
</head>
<body>
<h1>RAG answer report</h1>
<p class="muted">Airflow and Spark troubleshooting assistant. Generated {datetime.now():%Y-%m-%d %H:%M} from data/run_results.json.
Check each answer against the chunks shown under it.</p>
{summary_block()}
{cards}
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Step 4: One card per question
# ---------------------------------------------------------------------------

# Build the HTML card for one result. i is the question number (starting at 1).
def card(i, r):
    # Status tags: answered or refused (and who refused), plus the red flag if any
    tags = ""
    if r["error"]:
        tags += f'<span class="tag flag">ERROR: {esc(r["error"])}</span>'
    elif r["refused"]:
        tags += f'<span class="tag refused">REFUSED by {esc(r["refused_by"])}</span>'
    else:
        tags += '<span class="tag answered">ANSWERED</span>'
    if flag(r) and flag(r) != "ERROR":
        tags += f'<span class="tag flag">{esc(flag(r))}</span>'
    expectation = "should be answerable" if r["expect"] else "unanswerable: should be refused"
    tags += f'<span class="muted">{esc(expectation)}</span>'

    # One line of numbers: how well the best chunk matched, which tools were searched, how long it took
    score = f'{r["best_dense_score"]:.3f}' if r["best_dense_score"] is not None else "n/a"
    meta = (f'<div class="muted">best dense score {score} (threshold {cfg["retrieval"]["relevance_threshold"]})'
            f' | tools searched: {esc(", ".join(r["tools"] or []))} | {r["latency_s"]} s</div>')

    # The answer, or the refusal message. The model's own reason for a refusal is not stored.
    answer = f'<div class="answer">{esc(r["answer"] or "(no answer)")}</div>'

    # The chunks the answer cited, with file path and heading
    cited = r.get("sources") or []
    cited_numbers = {s["n"] for s in cited}
    sources = ""
    if cited:
        items = "".join(f'<li>[{s["n"]}] {esc(s["source"])} | {esc(s["heading"])}</li>' for s in cited)
        sources = f"<div><strong>Cited sources</strong><ul>{items}</ul></div>"

    issues = ""
    if r.get("citation_issues"):
        items = "".join(f"<li>{esc(x)}</li>" for x in r["citation_issues"])
        issues = f'<div class="issues"><strong>Citation issues</strong><ul>{items}</ul></div>'

    # Did the expected sources show up among the retrieved chunks? (a file-name match only,
    # so it says nothing about whether the right passage was retrieved)
    retrieved = r.get("retrieved") or []
    found = ""
    if r["expect"]:
        marks = [("found" if any(e in c["source"] for c in retrieved) else "MISSING") + " " + e for e in r["expect"]]
        found = f'<div class="muted">expected sources (file names): {esc("; ".join(marks))}</div>'

    # Every chunk the model was shown, collapsed. The n is the number the answer cites.
    chunk_items = ""
    for c in retrieved:
        mark = " (cited)" if c["n"] in cited_numbers else ""
        chunk_items += (f'<details><summary>[{c["n"]}] {esc(c["source"])} | {esc(c["heading"][:80])}{mark}</summary>'
                        f'<pre>{esc(c["text"])}</pre></details>')
    chunks = f"<details><summary>Retrieved chunks ({len(retrieved)})</summary>{chunk_items}</details>" if retrieved else ""

    return (f'<div class="card" id="q{i}"><h2>{i}. {esc(r["question"])}</h2>'
            f"<div>{tags}</div>{meta}{answer}{sources}{issues}{found}{chunks}</div>")

# Build every card, wrap them in the page, and write the file
cards = "".join(card(i, r) for i, r in enumerate(results, start=1))
out_path = data_dir / "report.html"
out_path.write_text(build_page(cards), encoding="utf-8")
print("wrote", out_path)

