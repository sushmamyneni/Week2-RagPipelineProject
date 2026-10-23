# Final summary: Airflow and Spark docs assistant (RAG)

A prototype that answers questions about Apache Airflow 3.3.2 and Apache Spark 4.2.0 using only their documentation, cites the chunks it used, and refuses ("not in the docs") when the docs do not cover the question. Built on 2026-10-21 and 2026-10-22, one stage at a time, with the code written by the project owner and guided step by step.

Companion files: `findings.md` (detailed results per stage), `pipeline.md` and `pipeline.html` (diagrams), `data/report.html` (the answers for the hand check).

---

## 1. What it does

1. Takes a question.
2. Searches 3,994 documentation chunks with two methods (meaning-based and keyword-based) and merges the results.
3. If even the best chunk is a poor match, it refuses without calling the chat model.
4. Otherwise it gives the best chunks, numbered, to a chat model, which answers only from them, puts a `[n]` citation after each claim, or says the docs do not cover the question.
5. The code checks the citations and lists the cited sources with file path and heading.

## 2. Tech stack

| Layer | Tool | Version | Role |
|---|---|---|---|
| Language | Python | 3.12.10 | Everything |
| Environment | uv, venv | | Virtual environment and installs (PowerShell on Windows) |
| Orchestration | LangGraph | 1.2.16 | The retrieve, then refuse or generate flow |
| LLM framework | LangChain core | 1.6.13 | Message types and the chat interface |
| Chat model connector | langchain-nebius (`ChatNebius`) | 0.1.3 | Calls the chat model on Nebius |
| Embedding connector | langchain-huggingface, sentence-transformers | 1.2.3, 6.2.0 | Runs the embedding model locally (on PyTorch 2.14.1) |
| Vector database | Pinecone serverless, `pinecone` client | 7.3.0 | Stores and searches the vectors |
| Keyword search | rank-bm25 | 0.2.2 | BM25 over the chunks, in memory |
| Chunking | langchain-text-splitters (`RecursiveCharacterTextSplitter`) | 1.1.3 | 1000 characters, 200 overlap |
| Structured output | Pydantic | 2.14.1 | The model's reply form (`answerable`, `answer`, `cited`) |
| Configuration | PyYAML, python-dotenv | 6.0.3, 1.2.4 | `config.yaml` for settings, `.env` for keys |
| Web UI | Streamlit | 1.66.1 | The question page |
| Diagrams | Mermaid | 11.4.1 (CDN, in `pipeline.html`) | Pipeline and architecture diagrams |

Installed but not used by the pipeline itself: `langchain-pinecone` (the raw `pinecone` client is used instead) and `langchain-openai` (it only provides the `openai` client that `1a_ListOfModels.py` uses to list Nebius models).

## 3. Models

| Purpose | Model | Where it runs | Why |
|---|---|---|---|
| Chat (writes the answer) | `Qwen/Qwen3-235B-A22B-Instruct-2507` | Nebius, endpoint `https://api.tokenfactory.nebius.com/v1/` | Large instruct model that follows "answer only from the context" rules; no hidden reasoning tokens, so the 650-token cap is not eaten. Temperature 0.2, max 650 tokens |
| Embeddings (meaning search) | `BAAI/bge-small-en-v1.5`, 384 dimensions | Locally on the CPU | Free, no key, no rate limits. Accepts about 512 tokens, so 1000-character chunks are not cut off. Questions get the bge query prefix; chunks do not |

The chat model is called only for questions that pass the relevance threshold. The embedding model is used at indexing time and for every question.

## 4. What was done, in sequence

### Stage 1: Setup and smoke test
- Created the venv with `uv`, `requirements.txt` (with a comment per package), `.gitignore` (written before `.env`), `.env` for keys, and `config.yaml` for every tunable value. Keys were never printed; they were only checked with `bool(...)`.
- Smoke-tested chat, structured output, embeddings and Pinecone, including the namespace argument.
- **Model changes on the way:** the plan said Nebius for chat and embeddings. The OpenAI account turned out to have no credits (429 `insufficient_quota`, which looks like a rate limit but is not), so OpenAI was dropped, and embeddings moved to a free local model. Chat went back to Nebius with Qwen3-235B. `ChatNebius` needed `nebius_api_base` set explicitly.
- Created the Pinecone index `airflow-spark-rag` (serverless, `aws` / `us-east-1`, 384 dimensions, cosine) after the owner's confirmation. An existing index from another project (dotproduct) was left untouched.

### Stage 2: Manifest (`2_build_manifest.py` -> `data/manifest.json`)
- Recorded the version, git commit SHA, and the keep and skip decisions for each docs folder. Keep and skip lists live in `config.yaml`; skip wins; a file matching neither is skipped.
- Airflow 3.3.2 (commit `aa19d2db`): 137 `.rst` files kept, 16 skipped. Spark 4.2.0 (commit `32f72996`): 44 `.md` files kept, 194 skipped. The Corpus repositories were verified unchanged with `git status`.

### Stage 3: Clean and chunk (`3a_build_chunks.py` -> `data/chunks.jsonl`, check with `3b_show_chunks.py`)
- **Airflow cleaning:** license comment, images and `exampleinclude` directives, `toctree` blocks, bare labels, substitution placeholders and roles removed; `list-table` blocks converted to text rows; code blocks and notes kept.
- **Spark cleaning:** Jekyll front matter cut (the page title was kept as a heading, since it only existed in the front matter), Liquid tags removed (`highlight` became code fences), `{{site.X}}` placeholders filled from the docs' own `_config.yml`, and HTML tables converted to text rows so each config property keeps its default and description together.
- Split at headings (tracking the heading path), then into 1000 characters with 200 overlap. Each chunk has an id (stable), text starting with its heading path, tool, version, source, heading path, content hash, and `groups: ["public"]`.
- **3,994 chunks** (Airflow 2,229, Spark 1,765). Eight stub or generated files were skipped as too short.

### Stage 4: Index (`4a_index_chunks.py`)
- Embedded the chunks locally in batches (about 6 minutes) and upserted them to Pinecone, namespace `docs-v1`, with the chunk text stored in the metadata.
- Dry run is the default; `--write` is required to write. "Already indexed" is decided by fetching ids from Pinecone and comparing `content_hash`, so a second run embedded nothing new. Stale vectors are deleted and the count is verified.
- Check passed: **3,994 vectors = 3,994 chunks**, and the second run reported `to embed: 0`.

### Stage 5: Retrieval (`5a_retrieval.py`, check with `5b_run_questions.py`)
- **Dense search** with the bge query prefix, per tool, top 8. **BM25** over `chunks.jsonl` with stopwords removed and dotted config keys kept whole and split. **Reciprocal rank fusion** merges the two lists (rank-based, because the score scales differ).
- **Which tool to search:** a tool takes part only if its best score is within 0.04 of the overall best. One tool gets 6 slots; a cross-tool question gets 10 (5 + 5), added after the first full run showed 6 slots were too few for questions spanning both tools.
- **Relevance threshold 0.65** on the best dense score. It refuses clearly off-topic questions (Snowflake scored 0.56) but cannot separate near-topic ones: PostgreSQL scored 0.76, above the lowest real question (0.73), because the Airflow docs describe setting up a PostgreSQL database. That case is left to the model.
- 20 test questions in `config.yaml`: 15 answerable (6 Airflow, 7 Spark, 2 cross-tool) and 5 unanswerable. The plan listed nine; 11 were added, each checked against the chunks first.

### Stage 6: LangGraph (`6a_rag_graph.py`, runner `6b_run_questions.py`)
- Graph: `retrieve` -> conditional edge -> `generate` or `refuse`. The refuse node returns a fixed message with no model call. The generate node shows the model the numbered chunks and gets structured output: `answerable`, `answer` with inline `[n]` markers, and `cited`. A model refusal returns exactly the same reply as a threshold refusal.
- The prompt (in `config.yaml`) says: use only the context, a chunk that only mentions a topic is not an answer, cite every claim, list the cited numbers.
- **Citation check:** every cited number must exist, the markers in the text must equal the `cited` list, and every sentence must carry a marker. It checks consistency, not whether a chunk really supports a claim.
- The runner times each question and saves everything to `data/run_results.json`.

### Stage 7: Report (`7a_build_report.py` -> `data/report.html`)
- One page with a summary and one card per question: the answer or refusal, who refused, cited sources, citation issues, scores, latency, and every chunk the model saw (full text, cited ones marked). No model calls, all text HTML-escaped, no internet needed.
- Optional verdict buttons were skipped on purpose; verdicts go in the table in `findings.md`.

### Stage 8: Web UI (`8a_app.py`), partly built
- Streamlit page: question box, Ask button, answer, caption with best score, tools searched and time, expandable cited sources with chunk text, and a citation warning. The graph is cached so the models load once. Tested headlessly: the Snowflake question was refused by the threshold in 0.9 s, and the retries question was answered with two cited sources in 2.4 s.
- **Not built yet:** the tool filter (needs a `tool` key in the graph state and a one-line change in `retrieve_node`), clickable example questions, and friendly error messages.

## 5. Results (latest full run of the 20 questions)

| | |
|---|---|
| Answered / refused | 15 / 5 |
| Unanswerable questions refused | 5 of 5 (Snowflake by threshold; PostgreSQL, dbt, Databricks, Redshift by the model) |
| Wrong refusals / answers that should have been refused | 0 / 0 |
| Errors | 0 |
| Average latency | 2.1 s answered (max 3.5 s), 0.9 s refused (0.3 s for a threshold refusal) |
| Answers with citation warnings | 2 (#5 and #15; "sentence without a citation") |

The hand check of the answers against the chunks has not been done yet; the table is in `findings.md`.

## 6. Problems met and how they were solved

| Problem | Cause | Fix |
|---|---|---|
| OpenAI calls returned 429 | No credits on the account (`insufficient_quota`) | Local embeddings; Nebius for chat; retry logic treats this as a stop condition |
| `Missing credentials` from the OpenAI client | `.env` was not loaded into the environment | `load_dotenv()` before reading the key |
| `langchain` import errors, empty package list | Install had not run in the active venv | `uv pip install -r requirements.txt` |
| Spark sections had no parent heading | Page title and body headings were both `#` | Title treated as level 0 |
| BM25 matched "How do I..." FAQ headings | Common words scored | Stopword list in the tokenizer |
| Cross-tool question refused (#7) | Only 3 slots per tool; key chunks (Airflow Retry Policies, `spark.task.maxFailures`) ranked just below the cut | 10 slots when both tools take part; #7 now answered and grounded |
| Stage 5's "expected found" passed while #7 failed | It compared file names only | Documented as a weak check; the report shows the real chunks |
| Retrieval alone could not refuse PostgreSQL | Dense similarity measures topic, not coverage | The generation prompt refuses; verified on all four near-topic questions |
| Diagram nodes showed "Unsupported markdown: list" | Mermaid reads labels starting `1. ` as lists | Labels changed to `Stage 1: ...` |
| Files not found / empty after saving | Editor tab not saved, or a different file name | Checked the folder listing; file naming convention: stage number plus a letter when a stage has several files |
| PowerShell issues | No `&&`; `*>` writes UTF-16; Python typed into the prompt | Separate commands; read output files as UTF-16 or use `Out-File -Encoding utf8` |

## 7. Known limits and open items

- **Near-topic questions depend on the prompt.** Four of the five unanswerable questions pass the 0.65 threshold and are refused only by the model. No threshold separates them (the lowest real question, 0.733, is below PostgreSQL at 0.759). If the model ever answers from its own knowledge, the options are a stricter prompt, a lexical check, or a verify step (out of scope so far).
- **Small test set.** 20 questions set `relevance_threshold` (0.65) and `tool_margin` (0.04); the margin sits between 0.030 (cross-tool) and 0.051 (closest single-tool question), so it is thin. Only two cross-tool questions back `cross_tool_final_top_k`.
- **Citations show where, not whether.** The check proves consistency. Example: the retries answer cites an `assets.rst` chunk that is only loosely about retries, and #7's answer adds barrier-mode settings that do not belong to a plain spark-submit job. Only the hand check judges those.
- **Answers vary** a little between runs (temperature 0.2). A model refusal's own reason is not stored.
- **Corpus gaps by choice:** Airflow generated `*-ref` pages and release notes, Spark `ml-*`, `mllib-*`, `sparkr*`, `graphx*`, `sql-ref-*`, the older DStream `streaming-*.md` pages. Cleaning leftovers: a few Airflow directives (`include`, `literalinclude`, `mermaid`, `versionadded`) and HTML tags outside tables in Spark.
- **Output cap:** the reply is JSON inside a 650-token cap; a very long answer could be cut off (not seen in 20 questions).
- **Housekeeping:** `out.txt` and `run.txt` in `week2-rag` are scratch files and can be deleted. The header comment of `7a_build_report.py` still lists steps that were skipped. `findings.md` has not yet been updated for the `cross_tool_final_top_k` change. The UI steps above are unfinished. The corpus folders for dbt and PostgreSQL docs are untouched and not indexed.
- **Out of scope throughout:** dbt and PostgreSQL docs, query rewrite, chat memory, a verify step, automated scoring.

## 8. How to run it

From `C:\MyGit\Week2-BuildRAGProject\week2-rag`, with the venv active and `.env` filled in:

```powershell
uv pip install -r requirements.txt     # once
python 1b_create_index.py              # creates the Pinecone index if it does not exist
python 2_build_manifest.py             # data/manifest.json
python 3a_build_chunks.py              # data/chunks.jsonl
python 3b_show_chunks.py 20            # look at 20 random chunks
python 4a_index_chunks.py              # dry run
python 4a_index_chunks.py --write      # embeds (about 6 minutes) and writes to Pinecone
python 5b_run_questions.py             # retrieval check, no model calls
python 6a_rag_graph.py                 # quick test of the graph
python 6b_run_questions.py             # all 20 questions through the graph (about 19 chat calls)
python 7a_build_report.py              # data/report.html
streamlit run 8a_app.py                # the question page at http://localhost:8501
```

## 9. Files

| File | Purpose |
|---|---|
| `config.yaml` | Every tunable value: models, chunking, retrieval, generation and prompts, indexing, Pinecone, corpus keep and skip lists, 20 test questions |
| `.env` | API keys (git-ignored; never printed) |
| `requirements.txt` | Dependencies, with a comment each |
| `1a_ListOfModels.py`, `1b_create_index.py`, `1c_smoke_test.py` | Stage 1 |
| `2_build_manifest.py` | Stage 2 |
| `3a_build_chunks.py`, `3b_show_chunks.py` | Stage 3 |
| `4a_index_chunks.py` | Stage 4 |
| `5a_retrieval.py`, `5b_run_questions.py` | Stage 5 |
| `6a_rag_graph.py`, `6b_run_questions.py` | Stage 6 |
| `7a_build_report.py` | Stage 7 |
| `8a_app.py` | Stage 8 (UI, partly built) |
| `data/` (git-ignored) | `manifest.json`, `chunks.jsonl`, `run_results.json`, `report.html` |
| `findings.md`, `pipeline.md`, `pipeline.html`, `final_summary.md` | Notes, diagrams, this summary |
