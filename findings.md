# Findings

No key values were printed anywhere; keys were only checked with `bool(...)`.

Scripts, in run order: `1a_ListOfModels.py`, `1b_create_index.py`, `1c_smoke_test.py`, `2_build_manifest.py`, `3a_build_chunks.py`, `3b_show_chunks.py`, `4a_index_chunks.py`, `5a_retrieval.py`, `5b_run_questions.py`, `6a_rag_graph.py`, `6b_run_questions.py`, `7a_build_report.py`.

# Stage 1: setup and smoke test (prepare the environment and prove every external service works before building on it: venv, config, and one test call each to chat, embeddings, structured output and Pinecone)

Scripts: `1a_ListOfModels.py` (list the Nebius models), `1b_create_index.py` (create the Pinecone index), `1c_smoke_test.py` (the four test calls).

## Chat
- Class: `langchain_nebius.ChatNebius`
- Model: `Qwen/Qwen3-235B-A22B-Instruct-2507` (Instruct variant, no hidden reasoning tokens)
- `nebius_api_base` must be set explicitly to `https://api.tokenfactory.nebius.com/v1/` (stored in `config.yaml`)
- Test call returned `OK`, finish reason `stop`

## Structured output
- `llm.with_structured_output(Answer)` works with the default method
- Returned a populated Pydantic object: `Answer(answer='Yes, the sky is blue.', confident=True)`

## Embeddings
- Local model: `BAAI/bge-small-en-v1.5` via `langchain-huggingface`
- Vector length: **384**
- No key or billing needed. First run downloads the model (about 130 MB). The `HF_TOKEN` warning is harmless
- OpenAI embeddings were dropped: the OpenAI account has no credits (429 `insufficient_quota`)
- bge models want the prefix `Represent this sentence for searching relevant passages: ` on questions only, not on chunks (done in Stage 5)

## Pinecone
- Serverless, `aws` / `us-east-1`
- Index created: `airflow-spark-rag`, dimension 384, metric cosine (read from `config.yaml` by `1b_create_index.py`)
- Existing index `data-lineage-graphrag-local-embeddings` (384, dotproduct) belongs to another project and was left untouched
- Namespace argument works: upsert and query with `namespace="smoke-test"` found the record; a query in `other` returned nothing
- Serverless is eventually consistent: wait a few seconds after upsert before querying

## Decisions and changes from the original plan
- Chat and embeddings both via Nebius was the original plan; embeddings are local instead
- Index dimension comes from `embedding.dimensions` in `config.yaml` (single source of truth)
- Retry logic must treat `insufficient_quota` as a stop condition, not a transient error

# Stage 2: manifest (record exactly which doc files and versions are used: git commit, version, and the keep and skip decisions)

Script: `2_build_manifest.py` -> `data/manifest.json`.

| | Airflow | Spark |
|---|---|---|
| Version | 3.3.2 (`airflow-core/pyproject.toml`) | 4.2.0 (`docs/_config.yml`) |
| Commit | `aa19d2dbb9ed187ec01957a92848d9b005e9ba9d` | `32f7299601108917fb01920a54e084595b7b3bf8` |
| Kept files | 137 `.rst` | 44 `.md` |
| Skipped files | 16 | 194 |

- Keep and skip lists live in `config.yaml` (`corpus` section). Skip wins over keep; a file matching neither is skipped (keep is an allow-list)
- The git tag `task-sdk/1.3.2` is for another package in the Airflow monorepo, so the version came from `pyproject.toml`
- Skipped on purpose: Airflow generated `*-ref` pages, release notes, license and navigation pages; Spark `ml-*`, `mllib-*`, `sparkr*`, `graphx*`, `sql-ref-*`, site assets, and the older DStream `streaming-*.md` pages
- The Corpus repos were checked with `git status` and are unchanged

# Stage 3: clean and chunk (turn the kept docs into clean, labelled pieces of text, each about 1000 characters, ready to embed)

Scripts: `3a_build_chunks.py` -> `data/chunks.jsonl`, check with `3b_show_chunks.py`.

## Result
- **3,994 chunks**: Airflow 2,229, Spark 1,765
- IDs unique, no duplicate texts, longest chunk about 1,200 characters, median about 800
- Chunk size 1000, overlap 200, split on blank lines, then line ends, then spaces (`RecursiveCharacterTextSplitter`)
- Each chunk: `id`, `text`, `tool`, `version`, `source`, `heading_path`, `content_hash`, `groups` (`["public"]`)
- The text starts with the heading path, e.g. `Tuning Spark > Memory Tuning > Garbage Collection Tuning`, then a blank line, then the body
- `id` = tool + first 16 hex of sha1(source + position), so it is stable between runs; `content_hash` = sha256 of the text and changes when the text changes

## 8 files skipped as too short after cleaning (under `chunking.min_doc_chars` = 300)
- Airflow index pages: `administration-and-deployment/index.rst`, `authoring-and-scheduling/index.rst`, `howto/index.rst`, `howto/operator/index.rst`, `tutorial/index.rst`
- Spark: `sql-error-conditions.md` (generated page, content was an include), `structured-streaming-kafka-integration.md` and `structured-streaming-programming-guide.md` (redirect stubs; the real pages are in `streaming/`, so there is no duplicate)
- Sections under `chunking.min_section_chars` = 50 (heading-only sections) produce no chunk; their children carry the heading path

## Airflow cleaning rules (`clean_rst`)
1. Remove the Apache license comment
2. Remove `image`, `figure` and `exampleinclude` directives with their option lines
3. Remove `toctree` blocks (navigation only)
4. Remove bare `.. _label:` lines, including labels that contain colons
5. Remove `|placeholder|` lines and lone `|` lines
6. Reduce roles to their text (`:meth:`~pkg.Cls`` becomes `` `pkg.Cls` ``)
7. Convert `list-table` blocks to rows: `- Header: cell; Header: cell`
8. Collapse blank lines. `code-block` and `note` directives are kept

## Spark cleaning rules (`clean_md`)
1. Cut the Jekyll front matter but keep `displayTitle` as a `#` heading (the title exists only in the front matter)
2. Drop `{% comment %}` blocks, `include_example` and `include_api_gen` lines, and other Liquid tags
3. Turn `{% highlight lang %}` into code fences, keeping the code
4. Fill in `{{site.X}}` placeholders from `docs/_config.yml` (e.g. `SPARK_VERSION` = 4.2.0)
5. Remove `{:toc}` and `{:.no_toc}` markers
6. Convert HTML tables to rows: `- Property Name: `spark.x`; Default: ...; Meaning: ...; Since Version: ...` (this keeps the 391 config rows in `configuration.md` whole)

## Known leftovers
- Airflow: `include`, `literalinclude`, `mermaid`, `contents`, `versionadded` and `grid-item-card` directives are still in the text; link targets like `.. _name: URL` are kept
- Spark: HTML tags outside tables (`<code>`, `<a>`, `<div>`) are still in the text
- The 200-character overlap means some chunks start mid-sentence
- Heading detection: Markdown ignores `#` inside code fences; RST headings must start at column 0

# Stage 4: index (turn each chunk into a vector and store it in Pinecone, so questions can later find chunks by meaning)

Script: `4a_index_chunks.py`.

## Result
- Namespace `docs-v1` of index `airflow-spark-rag` holds **3,994 vectors**, equal to the chunk count (`match: True`)
- Second run: `already indexed: 3994 | to embed: 0`, finished in seconds (embeds nothing new)
- Deleted stale vectors: 0

## Timing
- Local embedding: about 12 to 14 seconds per 128 chunks, so **about 6 minutes** for all 3,994 chunks on this CPU
- Upsert: 40 batches of 100

## How it works
- Dry run is the default; nothing is written unless `--write` is passed. `--limit N` embeds only the first N chunks for quick timing
- "Already indexed" is decided by fetching each id from Pinecone and comparing `content_hash`, so Pinecone is the source of truth
- Each vector carries metadata: `text`, `tool`, `version`, `source`, `heading_path`, `content_hash`, `groups`. The chunk text is stored so Stage 5 can read it straight from a search result
- Upserts retry on HTTP 429, 5xx and network errors with doubling waits; other 4xx errors (bad key, wrong dimension) stop at once
- Vectors whose id is no longer in `chunks.jsonl` are deleted, then the namespace count is polled until it settles
- Batch sizes and retry settings are in `config.yaml` (`indexing` section)

# Stage 5: retrieval (given a question, find the best 6 chunks and decide whether the docs cover it at all)

Scripts: `5a_retrieval.py` (the search functions), `5b_run_questions.py` (runs the test questions and prints sources and scores).

## Result
- 20 test questions in `config.yaml` (`test_questions`): 15 answerable (6 Airflow, 7 Spark, 2 cross-tool) and 5 unanswerable. The plan listed 9; 11 were added, each checked against the chunks first. Each has an `expect` list of source paths that should appear in the results
- **All 15 answerable questions** returned an expected source in the top 6, including both cross-tool questions (Airflow and Spark sides)
- Relevance threshold `retrieval.relevance_threshold` = **0.65** on the best dense (cosine) score. Only Snowflake is refused:

| Best dense score | Question | Answerable? |
|---|---|---|
| 0.564 | Snowflake credit cost | no, refused |
| 0.660 | Databricks cluster cost | no, passes |
| 0.679 | dbt incremental model | no, passes |
| 0.729 | Redshift workload management | no, passes |
| 0.733 | Obscure task failures (lowest real question) | yes |
| 0.759 | PostgreSQL autovacuum | no, passes |
| 0.766 to 0.880 | The other real questions | yes |

## How it works
- **Dense search:** the question is embedded with the bge query prefix (`retrieval.query_prefix`), then Pinecone returns the top 8 by cosine similarity, optionally filtered by `tool`
- **BM25:** `rank-bm25` over `chunks.jsonl`, in memory. The tokenizer drops stopwords (without them, "How do I ..." matched unrelated FAQ headings) and keeps dotted config keys whole while also splitting them (`spark.sql.shuffle.partitions` and `shuffle`, `partitions`)
- **Merge:** reciprocal rank fusion (`rrf_k` = 60). Only ranks are used, because dense and BM25 scores are on different scales. Chunks found by both searches rank first
- **Which tool to search:** dense search runs once per tool, and a tool takes part only if its best score is within `tool_margin` (0.04) of the overall best. One tool gets all 6 slots, two tools get 3 each. For single-tool questions the other tool scored 0.05 to 0.15 lower; for the cross-tool question the gap was 0.030
- `retrieve(question, tool=None)` returns `chunks`, `tools`, `best_dense_score` and `relevant` (best dense score >= threshold)
- The best dense score, and not the BM25 or RRF score, is what the threshold uses, because dense scores are comparable across questions

## What went wrong and how it was fixed
- A fixed 3 + 3 split between the tools pushed `troubleshooting.rst` (rank 6 to 7 in both lists) out of the "obscure task failures" results. The margin rule above fixed that and also removed stray chunks from the other tool

## Known limits
- Dense similarity measures topic, not whether a chunk answers the question. PostgreSQL scores 0.759 because Airflow documents setting up a PostgreSQL database, though nothing covers autovacuum. **Four of the five unanswerable questions pass the threshold**, so Stage 6's prompt must refuse when the retrieved text does not contain the answer
- No threshold separates all cases: the lowest real question (0.733) is below PostgreSQL (0.759) and close to Redshift (0.729). 0.65 is a safety net for clearly off-topic questions. Raising it to about 0.72 would also refuse Databricks and dbt, but only 0.011 below the lowest real question
- `tool_margin` 0.04 sits between 0.030 (cross-tool) and 0.051 (closest single-tool question), so it is thin and rests on a small sample
- `5a_retrieval.py` starts with a digit, so Stage 6 imports it with `importlib.import_module("5a_retrieval")`

# Stage 6: LangGraph (answer a question from the retrieved chunks, or refuse, and cite the chunks used)

Scripts: `6a_rag_graph.py` (the graph), `6b_run_questions.py` (runs the test questions, times each one, saves `data/run_results.json`).

## Result
- 20 questions run from the command line: **14 answered** (average 2.2 s, maximum 3.3 s) and **6 refused** (average 1.1 s); 0 errors
- **All 5 unanswerable questions were refused**: Snowflake by the score threshold (0.35 s, no model call), and PostgreSQL, dbt, Databricks and Redshift by the model (about 1.1 to 1.5 s). Those four pass the 0.65 threshold, so the prompt is what stopped them
- **1 wrong refusal**: #7, the cross-tool question "An Airflow task that runs spark-submit fails intermittently. Which retry and failure settings should I check?" (see below)
- 4 answers carry a citation warning (#1, #5, #15, #17). All are "sentence without a citation": the first sentence states the claim and the next sentence carries the `[n]` marker. The cited numbers were always valid
- The 3-question trial showed 7.5 s for the first question; that includes one-time warm-up (first query through the embedder, first connections to Pinecone and Nebius)

## How it works
- LangGraph graph with a shared state (`RagState`): `retrieve` node, then a conditional edge (`route`) to either `generate` or `refuse`
- **retrieve** calls `retrieve()` from `5a_retrieval.py` (imported with `importlib.import_module`, since the file name starts with a digit). It fills in the chunks, tools, best dense score and the `relevant` flag
- **route**: `relevant` (best dense score at least 0.65) goes to `generate`, otherwise to `refuse`
- **refuse**: returns `generation.refusal_message` from config. No model call
- **generate**: `ChatNebius` with `Qwen/Qwen3-235B-A22B-Instruct-2507`, temperature 0.2, max tokens 650. The chunks are shown numbered as `[n] source: <path>` followed by the chunk text. The reply is structured output: `Answer(answerable: bool, answer: str with [n] markers, cited: list[int])`. It tries twice if the reply is empty or malformed
- A model refusal (`answerable` false) returns exactly the same reply as a threshold refusal, so both look alike; `refused_by` in the results says which one it was (`threshold` or `model`)
- The prompt is in `config.yaml` (`generation.system_prompt`, `user_prompt`): use only the context, no outside knowledge; a chunk that only mentions a topic is not an answer; cite every claim with `[n]`; list the cited numbers
- **Citation check** (`check_citations`): every number must be between 1 and the number of chunks shown, the markers in the text must equal the `cited` list, and every sentence must carry a marker. It returns the cited sources (path and heading) and a list of issues, and never changes the answer
- **Runner** (`6b_run_questions.py`): `--limit N` runs only the first N questions. Per question it saves the answer, who refused, scores, tools, cited sources, citation issues, every retrieved chunk (path and heading) and the latency

## The wrong refusal (#7) and why
- The model was shown 6 chunks: two from `dag-level-retry-via-callback.rst`, `tasks.rst` > Special Exceptions, two Spark `configuration.md` chunks on barrier execution, and one from `running-on-kubernetes.md`
- The chunks that answer it were missing: `tasks.rst` > Retry Policies (Airflow side) and the `spark.task.maxFailures` row (Spark side). The model correctly said the shown context does not answer the question
- Stage 5's "expected found 2/2" for this question compared only file names (`tasks.rst` and `configuration.md` were both present, with the wrong chunks), so that check was too lenient
- So this is a retrieval problem, not a prompt problem. With 6 slots split 3 + 3 between the two tools, a cross-tool question has little room
- Options considered, none applied yet: leave it and record it in the Stage 7 hand check; give cross-tool questions more slots (changes `final_top_k` from the plan); soften the prompt (risk: weakens the refusals that worked 5 of 5)

## Known limits
- The citation check verifies consistency, not support. Example: the retries answer cited `assets.rst` for `@task.bash(retries=3)`, a real but loosely related chunk. Only the hand check can judge that
- Answers vary a little from run to run (temperature 0.2), so one run of a question is a sample
- The reply is JSON inside a 650-token cap. A very long answer could be cut off and fail to parse (the retry would hit the same cap). Not seen in the 20 questions
- The "sentence without a citation" flag is a heuristic and fires when a lead sentence is cited by the sentence after it

## Second run of the 20 questions (made so the results also hold the chunk text)
- `6b_run_questions.py` was changed to save each retrieved chunk's `id` and full `text`, and rerun. The numbers above are from the first run; the rerun gave: 14 answered (average 3.1 s), 6 refused (average 1.5 s; 1 by threshold, 5 by the model), 0 errors
- **#7 was refused again**, and it is still the only flagged question, so that failure is stable and not a one-off
- Citation warnings dropped from 4 answers to 2. Latencies were higher than in the first run (3.1 s against 2.2 s for answered questions), so treat them as a rough range, not fixed values

# Stage 7: report (turn the 20 results into one page you can check by hand: for each question the answer or refusal, the cited sources and the chunks the model was shown)

Script: `7a_build_report.py` reads `data/run_results.json` and writes `data/report.html`. It makes no model calls, so it is free and can be rerun. `run_results.json` comes from `6b_run_questions.py`.

## Result
- The page has a summary box and **20 question cards** (14 answered, 6 refused, 1 flagged: #7). It has 120 chunk blocks, 6 for each question
- Summary box: counts of answered and refused (by threshold and by model), errors, answers with citation issues, average latency for answered and for refused questions, and the flagged questions in red
- Each card shows: tags (answered, or refused by threshold or by model, plus a red `WRONG REFUSAL` or `SHOULD HAVE REFUSED` flag when the result goes against what was expected); the best dense score against the threshold; the tools searched; the latency; the answer or refusal message; the cited sources with file path and heading; any citation issues in red; and the expected sources
- Under each card, the retrieved chunks are listed collapsed with their full text; the chunks the answer cited are marked `(cited)`. Open a cited chunk next to its `[n]` marker to check a claim
- The "expected sources" line compares **file names only**, so it can say "found" when the key chunk was not retrieved (the #7 case). It is a pointer, not a check
- All text from the data (questions, answers, chunk text) is HTML-escaped, there is no script in the page, and it needs no internet access. A generic check found no raw `<` inside any chunk block

## Decisions
- Format: an HTML page, with the full chunk text, as chosen (this needed the runner change above)
- The planned verdict buttons, notes boxes and copy-out button (Step 5 of the report) were **skipped on purpose**. They are optional convenience and the plan does not need them. Verdicts are recorded by hand in the table below
- The header comment of `7a_build_report.py` still lists Steps 5 and 6 (controls and write); only Steps 2 to 4 are in the file, and the file write is part of Step 4

## Hand check (to fill in)
Open `data/report.html`, read each answer, open the cited chunks, and record a verdict: correct, partly correct, wrong; for a refusal, refusal right or refusal wrong.

| # | Question | Expected | Verdict | Note |
|---|---|---|---|---|
| 1 | How do I set retries on a task? | answerable | | |
| 2 | What is the difference between a sensor's timeout and its retries? | answerable | | |
| 3 | Where should I look when a task fails for an obscure reason? | answerable | | |
| 4 | How do I change the number of shuffle partitions in Spark? | answerable | | |
| 5 | How do I set the executor memory in Spark? | answerable | | |
| 6 | How do I monitor a running Spark application? | answerable | | |
| 7 | An Airflow task that runs spark-submit fails intermittently. Which retry and failure settings should I check? | answerable | | known: refused, key chunks not retrieved |
| 8 | What is the Snowflake credit cost? | refuse | | |
| 9 | How do I tune PostgreSQL autovacuum? | refuse | | |
| 10 | Why did my task fail with no logs in the UI? | answerable | | |
| 11 | How do I limit how many tasks run at the same time using pools? | answerable | | |
| 12 | How do I run a task even when an upstream task failed? | answerable | | |
| 13 | How do I enable dynamic allocation of executors in Spark? | answerable | | |
| 14 | How many times does Spark retry a failed task? | answerable | | |
| 15 | How do I submit a Spark application to YARN in cluster mode? | answerable | | |
| 16 | How do I enable adaptive query execution in Spark? | answerable | | |
| 17 | My Airflow task running a Spark driver keeps getting killed for running out of memory. What should I check? | answerable | | |
| 18 | How do I define an incremental model in dbt? | refuse | | |
| 19 | What does a Databricks cluster cost per hour? | refuse | | |
| 20 | How do I configure Redshift workload management queues? | refuse | | |

# Status
- Stage 1: complete (setup and smoke test)
- Stage 2: complete (manifest)
- Stage 3: complete (clean and chunk)
- Stage 4: complete (index (enbedding, storing in pinecone))
- Stage 5: complete (retrieval)
- Stage 6: complete (LangGraph: retrieve, then refuse below the threshold, otherwise write an answer with a citation per claim; 14 answered, 6 refused, 1 wrong refusal)
- Stage 7: complete (report: `data/report.html`, one card per question with the answer or refusal, cited sources and the retrieved chunks). The hand check of the 20 answers is yours to do; use the table above
- Open decision: the wrong refusal on #7 (cross-tool question). Options are in the Stage 6 section; none applied
