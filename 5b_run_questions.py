# Stage 5 check: run every test question through retrieval and print sources and scores.
# Use it to pick the relevance threshold: compare the best dense score of the answerable
# questions with the best dense score of the unanswerable ones.
import importlib
import yaml

cfg = yaml.safe_load(open("config.yaml"))

# The module name starts with a digit, so a normal "import" statement cannot be used
retrieval = importlib.import_module("5a_retrieval")

summary = []
for item in cfg["test_questions"]:
    question, expect = item["question"], item["expect"]
    out = retrieval.retrieve(question)

    # Paths of the chunks returned, shortened to the doc path
    sources = [c["source"].split("docs/", 1)[1] for c in out["chunks"]]
    # Which of the expected paths showed up in the top results
    found = [e for e in expect if any(e in s for s in sources)]
    verdict = "n/a (unanswerable)" if not expect else f"{len(found)}/{len(expect)} expected found"

    print("\n" + question)
    print(f'  best dense score: {out["best_dense_score"]:.3f} | relevant: {out["relevant"]} | tools: {out["tools"]} | {verdict}')

    for c in out["chunks"]:
        d = "-" if c["dense_score"] is None else f'{c["dense_score"]:.3f}'
        print(f'    dense {d} | {c["source"].split("docs/", 1)[1]} | {c["heading_path"][:55]}')
    summary.append((out["best_dense_score"], out["relevant"], verdict, question))

# Sorted by best dense score: the threshold goes in the gap between the answerable
# questions and the unanswerable ones
print("\nSUMMARY (lowest score first)")
for score, relevant, verdict, question in sorted(summary):
    print(f"  {score:.3f} | relevant={relevant} | {verdict} | {question[:60]}")

