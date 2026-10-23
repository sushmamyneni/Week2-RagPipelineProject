# Week 2 project: Airflow and Spark docs assistant (RAG, Track 2: LangChain + LangGraph)

**Builder:** [your name]  |  **Code:** [GitHub link]  |  **Demo video (5 min or less):** [video link]

## One-line summary

A web and command-line assistant that helps data engineers troubleshoot Apache Airflow 3.3.2 and Apache Spark 4.2.0 by answering how-to and failure questions from 181 pages of official documentation, with a `[n]` citation after every claim and a refusal ("not covered in the docs") when the docs do not answer; target 90% of answers fully supported by their cited chunks and 90% relevant, with every answer under 5 seconds.

---

## Part 1: The RAG framework

| Field | Answer |
|---|---|
| **Use case** | "Which setting, parameter or page answers my Airflow or Spark problem?" asked by data engineers on call for pipelines, in a Streamlit web page (and a command-line runner), with the sources one click away. |
| **Corpus** | The official Airflow core docs (`airflow-core/docs`, 153 `.rst` files, version 3.3.2, commit `aa19d2db`) and Spark docs (`docs`, 238 `.md` files, version 4.2.0, commit `32f72996`); 137 and 44 of them are indexed (181 files), in English. The Apache Software Foundation owns the source of truth; the repos were cloned read-only. |
| **Ingestion + cleaning** | Keep and skip lists in `config.yaml` select the files (an allow-list; generated reference pages, release notes, ML and R docs are skipped). Cleaning strips license blocks, Jekyll front matter, Liquid and reStructuredText directives, images and navigation blocks, fills `{{site.X}}` placeholders, and turns HTML tables and `list-table` blocks into one text row per entry so a config property stays with its default and description. Stub and generated pages under 300 characters are dropped. |
| **Ingestion + freshness** | A manifest records each docs version and git commit; re-indexing is incremental because each chunk has a stable id and a content hash, so only changed chunks are re-embedded and removed chunks are deleted. Refresh is manual (rebuild when a new docs release is cloned) and there is no freshness SLA, since this is a prototype. |
| **Chunking + embedding** | Split at headings (tracking the heading path), then 1000 characters with 200 overlap on blank lines, then line ends; the heading path is written at the top of each chunk's text. Embeddings are `BAAI/bge-small-en-v1.5` (384 dimensions, about 512 tokens of capacity, so 1000-character chunks are not truncated), run locally, because it is free and has no rate limits. Result: 3,994 chunks. |
| **Retrieve** | Pinecone serverless (cosine, namespace `docs-v1`) and BM25 are both used: **hybrid** retrieval, merged by reciprocal rank fusion. Top 8 from each list, 6 chunks kept (10 when the question spans both tools). A tool is searched only if its best dense score is within 0.04 of the best; no reranker. |
| **"I don't know" path (designed first)** | Three layers: (1) a relevance threshold of 0.65 on the best dense score refuses clearly off-topic questions without calling the model; (2) the prompt forbids outside knowledge and says a chunk that only mentions a topic is not an answer, so the model refuses near-topic questions; (3) a citation check flags out-of-range numbers, markers that disagree with the cited list, and uncited sentences. Both refusals return the same message. |

**Targets and measurements**

| Target | Value | Measured |
|---|---|---|
| Faithfulness (answers fully supported by their cited chunks, hand-checked) | 90% | [fill in after the hand check] |
| Relevance (an expected source among the retrieved chunks, 15 answerable questions) | 90% | 15 of 15, by file name only (a weak check; see the failure analysis) |
| Unanswerable questions refused | 100% | 5 of 5 |
| Latency ceiling per question | 5 s | average 2.1 s answered and 0.9 s refused, maximum 3.5 s |

---

## Project overview

**Problem.** Troubleshooting a pipeline means searching hundreds of pages of Airflow and Spark documentation, and a plain language model will answer confidently from memory even when the docs say nothing. This assistant answers only from the indexed docs, shows where each claim came from, and stays quiet when it cannot answer.

**What was built.**
- An offline pipeline (manifest, clean and chunk, embed and index) that turns the docs into 3,994 chunks and 3,994 vectors.
- An online graph (LangGraph): retrieve, then either refuse or generate a cited answer with a chat model.
- A command-line runner for a 20-question test set with timings, an HTML report with every answer, cited source and retrieved chunk for a hand check, and a Streamlit page for asking questions (answer, scores, timing, and each cited source with its full file path and chunk text).

**Stack.** Python 3.12, LangGraph 1.2, LangChain core 1.6, `langchain-nebius` (chat), `sentence-transformers` 6.2 on PyTorch (local embeddings), Pinecone serverless, `rank-bm25`, `langchain-text-splitters`, Pydantic (structured output), PyYAML and python-dotenv (config and keys), Streamlit 1.66. Chat model: `Qwen/Qwen3-235B-A22B-Instruct-2507` on Nebius, temperature 0.2, max 650 tokens. LangSmith tracing was not enabled.

**Architecture.** See `pipeline.html` (diagrams of the stages and of how scripts, files and services connect).

```
Offline:  docs -> manifest -> clean + chunk (3,994) -> local embeddings -> Pinecone (docs-v1)
Online:   question -> dense search per tool + BM25 -> rank fusion -> best score >= 0.65 ?
                          no  -> refuse (no model call)
                          yes -> chat model answers from numbered chunks, cites [n], or refuses
                       -> citation check -> answer + sources
```

## Datasets

| | Airflow | Spark |
|---|---|---|
| Source | `apache/airflow`, `airflow-core/docs` | `apache/spark`, `docs` |
| Version / commit | 3.3.2 / `aa19d2dbb9ed187ec01957a92848d9b005e9ba9d` | 4.2.0 / `32f7299601108917fb01920a54e084595b7b3bf8` |
| Format | reStructuredText | Markdown with Jekyll front matter, Liquid tags and HTML tables |
| Files in the docs folder / indexed | 153 / 137 | 238 / 44 |
| Chunks | 2,229 | 1,765 |

Indexed topics: Airflow core concepts, authoring and scheduling, how-to guides, administration and deployment, installation, tutorials, security, best practices, FAQ, troubleshooting; Spark configuration, tuning, SQL performance tuning, monitoring, job scheduling, cluster modes (standalone, YARN, Kubernetes), the RDD and SQL guides, data sources, structured streaming, security, and the web UI. Skipped on purpose: generated `*-ref` pages and release notes (Airflow); `ml-*`, `mllib-*`, `sparkr*`, `graphx*`, `sql-ref-*`, site assets and the older DStream pages (Spark).

**Test set (20 questions, in `config.yaml`):** 15 answerable (6 Airflow, 7 Spark, 2 spanning both) and 5 unanswerable (Snowflake credit cost, PostgreSQL autovacuum, dbt incremental models, Databricks pricing, Redshift workload management). Each answerable question was first confirmed against the chunks. The plan had nine questions; eleven were added when the first results showed the nine were too few to choose a threshold.

## Prompts

System prompt (in `config.yaml`, `generation.system_prompt`):

```
You answer questions about Apache Airflow and Apache Spark using ONLY the numbered
context chunks provided by the user.

Rules:
1. Use only information that is written in the context. Do not use outside knowledge,
   even if you know the answer.
2. If the context does not contain the answer, set answerable to false and say so in one
   sentence. A chunk that only mentions the topic (for example, a setup guide that names
   a database) is not an answer to a question about that topic.
3. Cite every claim with the number of the chunk it came from, like [1] or [2][3],
   placed at the end of the sentence that makes the claim.
4. In cited, list the number of every chunk you cited in the answer.
5. Be concise and concrete. Name settings, parameters and file names exactly as they
   are written in the context. If the question spans both Airflow and Spark, cover
   both and say which tool each point belongs to.
```

User prompt: `Context:` followed by the numbered chunks (`[n] source: <path>` and the chunk text), then `Question: <question>`.

Reply format (structured output): `answerable` (true or false), `answer` (text with inline `[n]` markers), `cited` (list of chunk numbers). Refusal message: "This is not covered in the Airflow and Spark docs I have." The inline markers give a citation per claim; the `cited` list is a cross-check, and `answerable` is a dependable refusal flag.

---

## Evaluation report (20 questions)

Latest full run (`6b_run_questions.py`). "Best score" is the cosine score of the closest chunk; the threshold is 0.65.

| # | Question | Should | Result | Best score | Sec | Citation issues |
|---|---|---|---|---|---|---|
| 1 | How do I set retries on a task? | answer | answered | 0.791 | 3.45 | 0 |
| 2 | What is the difference between a sensor's timeout and its retries? | answer | answered | 0.784 | 2.02 | 0 |
| 3 | Where should I look when a task fails for an obscure reason? | answer | answered | 0.733 | 1.72 | 0 |
| 4 | How do I change the number of shuffle partitions in Spark? | answer | answered | 0.820 | 2.71 | 0 |
| 5 | How do I set the executor memory in Spark? | answer | answered | 0.831 | 2.08 | 1 |
| 6 | How do I monitor a running Spark application? | answer | answered | 0.834 | 1.93 | 0 |
| 7 | An Airflow task that runs spark-submit fails intermittently. Which retry and failure settings should I check? | answer | answered | 0.791 | 2.70 | 0 |
| 8 | What is the Snowflake credit cost? | refuse | refused (threshold) | 0.564 | 0.30 | 0 |
| 9 | How do I tune PostgreSQL autovacuum? | refuse | refused (model) | 0.759 | 1.12 | 0 |
| 10 | Why did my task fail with no logs in the UI? | answer | answered | 0.820 | 2.21 | 0 |
| 11 | How do I limit how many tasks run at the same time using pools? | answer | answered | 0.818 | 2.70 | 0 |
| 12 | How do I run a task even when an upstream task failed? | answer | answered | 0.803 | 1.38 | 0 |
| 13 | How do I enable dynamic allocation of executors in Spark? | answer | answered | 0.873 | 2.15 | 0 |
| 14 | How many times does Spark retry a failed task? | answer | answered | 0.785 | 1.61 | 0 |
| 15 | How do I submit a Spark application to YARN in cluster mode? | answer | answered | 0.878 | 1.78 | 2 |
| 16 | How do I enable adaptive query execution in Spark? | answer | answered | 0.880 | 1.43 | 0 |
| 17 | My Airflow task running a Spark driver keeps getting killed for running out of memory. What should I check? | answer | answered | 0.766 | 2.16 | 0 |
| 18 | How do I define an incremental model in dbt? | refuse | refused (model) | 0.679 | 0.88 | 0 |
| 19 | What does a Databricks cluster cost per hour? | refuse | refused (model) | 0.660 | 1.29 | 0 |
| 20 | How do I configure Redshift workload management queues? | refuse | refused (model) | 0.729 | 0.93 | 0 |

**Summary:** 15 answered, 5 refused, 0 errors; no wrong refusals and no unanswerable question answered. Average latency 2.1 s answered (maximum 3.5 s), 0.9 s refused. Two answers (#5 and #15) have a citation warning, "sentence without a citation", where a lead sentence states the claim and the next sentence carries the marker; all cited numbers were valid.

**Hand check:** [fill in: for each answer, correct / partly correct / wrong; for each refusal, right / wrong. Add the counts here and compute the faithfulness figure above.]

### Failure analysis

1. **The threshold cannot separate near-topic questions.** The Snowflake question scores 0.564 and is refused by the threshold, but PostgreSQL scores 0.759, higher than the lowest real question (0.733), because the Airflow docs describe setting up a PostgreSQL database. Redshift (0.729), dbt (0.679) and Databricks (0.660) also pass 0.65. All four were refused by the model because of the prompt rule that a chunk which only mentions a topic is not an answer. So the refusal path depends on the prompt for these cases.
2. **A cross-tool question was wrongly refused at first (#7).** With 6 slots split 3 + 3 between the two tools, the chunks that answer it ranked just below the cut: Airflow's Retry Policies (dense rank 7, BM25 rank 3 for the "How it works" part) and the Spark `spark.task.maxFailures` row (dense rank 3). The model correctly said the six chunks shown did not answer it. Stage 5's check had passed it only because it compared file names, not chunks. Testing 6, 8 and 10 total slots showed that only 10 (5 + 5) recovers both chunks, so questions that span both tools now get 10. The rerun answered it with a grounded answer (`retries` and `retry_delay`, an `on_failure_callback` retry with an attempt limit, `spark.task.maxFailures` with its default of 4) and kept all five refusals.
3. **A fixed 3 + 3 split caused a regression** on "where to look for obscure task failures", which dropped from 2 of 2 expected sources to 0 of 2. The fix was the margin rule: search a tool only when its best score is within 0.04 of the best. That value sits between 0.030 (the cross-tool gap) and 0.051 (the closest single-tool gap), so it is thin and rests on a small sample.
4. **Citations show where, not whether.** The code check proves consistency only. For example the retries answer cites an `assets.rst` chunk that is only loosely about retries, and #7 adds Spark barrier-mode settings that do not belong to a plain spark-submit job. These are what the hand check is for.
5. **Answers vary a little between runs** (temperature 0.2); one run per question is a sample.

---

## Iterations

| # | What was tried | What happened | What changed |
|---|---|---|---|
| 1 | OpenAI chat and embeddings | Calls failed with 429 `insufficient_quota` (no credits; not a real rate limit) | Local `bge-small` embeddings (free); chat on Nebius; retry logic treats this error as a stop |
| 2 | Chat model choice | A first guess (Llama) was not on the account; "Flash" models risked spending the token cap on hidden reasoning | `Qwen3-235B-A22B-Instruct-2507`, a non-reasoning Instruct model; endpoint set explicitly |
| 3 | Cleaning the docs | Licenses, directives, Liquid tags, HTML tables and redirect stubs polluted the chunks | Format-specific cleaners (8 Airflow rules, 6 Spark rules), a minimum document size, and tables turned into rows |
| 4 | Splitting at headings | Spark page titles existed only in front matter, so sections had no parent | Title restored as a level-0 heading; heading path written at the top of each chunk |
| 5 | Dense search alone | Config keys and exact terms ranked poorly | Added BM25, merged by rank fusion (score scales differ) |
| 6 | BM25 on raw questions | "How do I..." matched unrelated FAQ headings | Stopword list in the tokenizer |
| 7 | One combined search | A cross-tool question got only one tool's chunks | Search per tool, then the 0.04 margin rule, then 10 slots when two tools take part |
| 8 | Choosing the threshold on 9 questions | Overlap between answerable and unanswerable scores | Threshold 0.65 as a safety net, refusal of near-topic questions moved into the prompt; test set grown to 20 |
| 9 | Free-text answers vs structured output | Free text needs a regular expression for citations and refusals | Structured output (`answerable`, `answer` with markers, `cited`) plus a citation check |
| 10 | Diagrams and report | Mermaid labels starting `1. ` rendered "Unsupported markdown: list" | Labels renamed `Stage 1: ...` |
| 11 | Streamlit page | Sources showed a shortened path that could not be found on disk | Each source shows its tool and the full file path |

## Learnings

- **Check chunks, not file names.** Retrieval checks that compare file names pass while the useful passage is missing; the report that shows the retrieved chunks next to each answer is what exposed the real failure.
- **Similarity measures topic, not coverage.** A near-topic question can score higher than a real one, so a threshold alone cannot decide when to refuse. The refusal had to be designed into the prompt and tested on near-topic questions.
- **Cross-tool questions need their own retrieval budget.** Splitting a small top-k between topics starves both.
- **Cleaning decides quality.** Keeping the table row together (property, default, description) and the heading path on every chunk mattered more than any model choice.
- **Know what a check proves.** The citation check proves consistency; only a person reading the chunk proves support.
- **Provider surprises are normal.** A billing error that looks like a rate limit, an endpoint that has to be set explicitly, and a model that was not on the account were all found by small smoke tests before anything depended on them.
- **Make every run safe to repeat.** Dry-run defaults, stable ids with content hashes, and a manifest of versions meant the index could be rebuilt without duplicates or paid calls by accident.

## How AI coding tools were used

I used Claude Code in VS Code as a pair programmer, working one small step at a time. For each step it proposed commented code and the expected output; I ran it and pasted back the result, and it read the output, inspected the data and files (read-only checks), and reviewed the code. It applied some edits directly when I asked, such as configuration changes, notes and diagrams. It was also used to test changes without the browser (a headless render of the diagrams, Streamlit's test runner) and to catch its own mistakes, for example a Mermaid label bug and an overly lenient test.

## Limitations and next steps

- The hand check of all 20 answers is not yet recorded; the faithfulness figure depends on it.
- The relevance threshold (0.65), tool margin (0.04) and the 10 cross-tool slots are tuned on 20 questions (only two spanning both tools) and could shift with more.
- Near-topic questions are refused by the model, not by retrieval. If a model ever answers from memory, the next options are a stricter prompt, a lexical check on key terms, a reranker, or a verification step.
- The UI still lacks a tool filter, clickable example questions and friendly error messages.
- LangSmith tracing and automated scoring were not added.
- Not covered by design: dbt and PostgreSQL docs, query rewriting, chat memory.

## Reproduce

From `week2-rag` with the virtual environment active and `.env` holding `NEBIUS_API_KEY` and `PINECONE_API_KEY` (never committed):

```
uv pip install -r requirements.txt
python 1b_create_index.py
python 2_build_manifest.py
python 3a_build_chunks.py
python 4a_index_chunks.py --write     # about 6 minutes of local embedding
python 6b_run_questions.py            # 20 questions through the graph
python 7a_build_report.py             # data/report.html
streamlit run 8a_app.py
```

Files: `config.yaml` (all settings, prompts and the test questions), `findings.md` (results per stage), `final_summary.md`, `pipeline.html` and `pipeline.md` (diagrams).

---

<!-- TO COMPLETE BEFORE SUBMITTING (delete this block):
1. Fill in your name, the GitHub link and the video link at the top.
2. Do the hand check in data/report.html, then fill in the "Hand check" paragraph and the faithfulness figure in the targets table.
3. Check the numbers in the evaluation table against your latest data/run_results.json (answers vary slightly between runs).
4. Confirm the use-case wording and the chosen track with the course form.
5. Make sure .env and data/ are not in the GitHub repo (they are in .gitignore). -->
