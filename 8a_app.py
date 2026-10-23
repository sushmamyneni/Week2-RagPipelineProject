# Stage 8: a web page for asking questions. A thin layer on top of the graph in 6a_rag_graph.py.
#
# What this file does when finished:
#   Step 1: Minimal page: a question box, an Ask button, and the answer
#   Step 2: Show the cited sources with their chunk text, and the scores and timing
#   Step 3: A tool filter (Airflow, Spark, or both)
#   Step 4: Example questions to click
#   Step 5: Friendly error messages
#
# Usage (run from the week2-rag folder, so config.yaml is found):
#   streamlit run 8a_app.py
import importlib
import time
from pathlib import Path

import streamlit as st
import yaml

# The corpus folder from config.yaml, used to show the full path of each source file
cfg = yaml.safe_load(open("config.yaml"))
corpus_dir = Path(cfg["paths"]["corpus_dir"])

# ---------------------------------------------------------------------------
# Step 1: Minimal page
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Airflow and Spark docs assistant")
st.title("Airflow and Spark docs assistant")

# Streamlit reruns this whole script on every click. Building the graph loads the embedder,
# the BM25 index and the chat client (several seconds), so it is cached: it runs once and
# every later question reuses it. The module name starts with a digit, hence importlib.
@st.cache_resource(show_spinner="Loading models (first time only)...")
def load_graph():
    return importlib.import_module("6a_rag_graph").build_graph()

app = load_graph()

question = st.text_input("Ask a question about Airflow or Spark")

# The button is True only on the run that follows a click
if st.button("Ask") and question.strip():
    # Time the whole question: search, and the model call when there is one
    start = time.perf_counter()
    with st.spinner("Searching the docs..."):
        result = app.invoke({"question": question.strip()})
    elapsed = time.perf_counter() - start
    st.write(result["answer"])

    # How the answer was produced: who refused (if anyone), the match score, tools, time
    if result["refused"]:
        by = "the score threshold" if not result["relevant"] else "the model"
        st.caption(f'Refused by {by}. Best match score {result["best_dense_score"]:.2f}. Tools searched: {", ".join(result["tools"])}. {elapsed:.1f} s.')
    else:
        st.caption(f'Best match score {result["best_dense_score"]:.2f}. Tools searched: {", ".join(result["tools"])}. {elapsed:.1f} s.')

    # The chunks the answer cites. The number is the [n] in the answer text.
    # Clicking one opens the full chunk text, so a claim can be checked against its source.
    if result["sources"]:
        st.subheader("Sources")
        for s in result["sources"]:
            chunk = result["chunks"][s["n"] - 1]   # n counts from 1, the list from 0
            # The chunk stores its path relative to the corpus folder (airflow/... or spark/...),
            # so joining it with the corpus folder gives the full path of the real file
            full_path = corpus_dir / chunk["source"]
            with st.expander(f'[{s["n"]}] {chunk["tool"]}: {s["source"]}  |  {s["heading"][:70]}'):
                st.caption(str(full_path))
                st.text(chunk["text"])

    # Problems found by the citation check, if any
    if result["citation_issues"]:
        st.warning("Citation check: " + "; ".join(result["citation_issues"]))
