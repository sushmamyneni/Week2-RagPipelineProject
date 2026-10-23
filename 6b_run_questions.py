# Stage 6 check: run the test questions through the RAG graph and time each one.
# Prints the answer, sources and citation issues, then a summary table, and saves
# everything to data/run_results.json for the Stage 7 report.
#
# Usage:
#   python 6b_run_questions.py              all questions in config.yaml (test_questions)
#   python 6b_run_questions.py --limit 3    only the first 3 (a cheap trial run)
import importlib
import json
import sys
import time
from pathlib import Path

import yaml

cfg = yaml.safe_load(open("config.yaml"))
questions = cfg["test_questions"]

# Optional: run only the first N questions
if "--limit" in sys.argv:
    questions = questions[:int(sys.argv[sys.argv.index("--limit") + 1])]

# The file name starts with a digit, so a normal "import" statement cannot be used.
# Importing it loads the embedder, the BM25 index and the chat client once.
rag = importlib.import_module("6a_rag_graph")
app = rag.build_graph()

results = []
for item in questions:
    q = item["question"]
    start = time.perf_counter()
    try:
        out = app.invoke({"question": q})
        error = None
    except Exception as e:
        # Keep going if one question fails. Only the error type is recorded, not its message.
        out, error = {}, type(e).__name__
    latency = time.perf_counter() - start

    refused = out.get("refused")
    # Who refused: the score threshold (no model call) or the model itself
    refused_by = None if not refused else ("threshold" if not out.get("relevant") else "model")
    results.append({
        "question": q,
        "expect": item["expect"],                 # [] means the question is unanswerable
        "error": error,
        "answer": out.get("answer"),
        "refused": refused,
        "refused_by": refused_by,
        "best_dense_score": out.get("best_dense_score"),
        "tools": out.get("tools"),
        "cited": out.get("cited"),
        "sources": out.get("sources"),            # the cited chunks, with path and heading
        "citation_issues": out.get("citation_issues"),
        # Every chunk the model was shown, in order, so you can check what it had available
        "retrieved": [{"n": i, "id": c["id"], "source": c["source"].split("docs/", 1)[1],
                "heading": c["heading_path"], "text": c["text"]}
                for i, c in enumerate(out.get("chunks", []), start=1)],

        "latency_s": round(latency, 2),
    })

    # Print this question as soon as it finishes
    print("\n" + q)
    if error:
        print("  ERROR:", error)
        continue
    print(f'  {"REFUSED (" + refused_by + ")" if refused else "ANSWERED"} | {latency:.1f}s | best dense score {out["best_dense_score"]:.3f} | tools {out["tools"]}')
    print("  answer:", out["answer"])
    for s in out["sources"]:
        print(f'  [{s["n"]}] {s["source"]} | {s["heading"][:60]}')
    if out["citation_issues"]:
        print("  citation issues:", out["citation_issues"])

# Summary table. The last column is only a pointer for your hand check:
# an unanswerable question (empty expect) should be refused, any other should be answered.
print("\nSUMMARY")
print(f'{"#":>2} {"sec":>6} {"result":<18} {"issues":>6} {"flag":<12} question')
for i, r in enumerate(results, start=1):
    if r["error"]:
        print(f'{i:>2} {r["latency_s"]:>6} ERROR {r["error"]}')
        continue
    result = "refused (" + r["refused_by"] + ")" if r["refused"] else "answered"
    should_refuse = not r["expect"]
    flag = "" if r["refused"] == should_refuse else ("WRONG REFUSAL" if r["refused"] else "SHOULD REFUSE")
    print(f'{i:>2} {r["latency_s"]:>6} {result:<18} {len(r["citation_issues"]):>6} {flag:<12} {r["question"][:55]}')

done = [r for r in results if not r["error"]]
answered = [r["latency_s"] for r in done if not r["refused"]]
refused_times = [r["latency_s"] for r in done if r["refused"]]
if answered:
    print(f"\nanswered: {len(answered)} | avg {sum(answered) / len(answered):.1f}s | max {max(answered):.1f}s")
if refused_times:
    print(f"refused:  {len(refused_times)} | avg {sum(refused_times) / len(refused_times):.1f}s")

# Save for the Stage 7 report
out_path = Path(cfg["paths"]["data_dir"]) / "run_results.json"
out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
print("wrote", out_path)
