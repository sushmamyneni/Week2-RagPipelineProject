# Stage 6: the RAG graph. Retrieve, then refuse or answer with citations.
#
# What this file does when finished:
#   Step 2: Define the graph's state and the retrieve node (calls Stage 5)
#   Step 3: The refuse node and the generate node (the chat model, structured output)
#   Step 4: Connect the nodes: retrieve, then refuse or generate, depending on relevance
#   Step 5: Check the citations the model gave against the chunks it was shown
#
# Usage:
#   python 6a_rag_graph.py      runs the quick test at the bottom of the file
import importlib
from typing import TypedDict
import os
import re
import yaml
from langgraph.graph import END, StateGraph

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_nebius import ChatNebius
from pydantic import BaseModel, Field

# Copy the keys from .env into environment variables. The key is read from the
# environment and never printed.
load_dotenv()

# Settings come from config.yaml, so nothing tunable is hardcoded here
cfg = yaml.safe_load(open("config.yaml"))
gen_cfg = cfg["generation"]

# ---------------------------------------------------------------------------
# Step 2: State and the retrieve node
# ---------------------------------------------------------------------------

# The file name starts with a digit, so a normal "import" statement cannot be used.
# Importing it loads the embedder and builds the BM25 index once.
retrieval = importlib.import_module("5a_retrieval")

# The state is the dictionary that travels through the graph. Each node receives it and
# returns only the keys it wants to change.
class RagState(TypedDict, total=False):
    question: str
    chunks: list         # the retrieved chunks, best first
    tools: list          # which tools were searched
    best_dense_score: float
    relevant: bool       # best dense score >= the threshold in config.yaml
    answer: str
    cited: list          # the chunk numbers the answer cites
    refused: bool        # True for a threshold refusal and for a model refusal
    sources: list           # path and heading of each cited chunk, for the report
    citation_issues: list   # problems found when checking the citations

# Retrieve node: run Stage 5 and put its results in the state.
# Nothing here calls the chat model.
def retrieve_node(state: RagState) -> dict:
    out = retrieval.retrieve(state["question"])
    return {
        "chunks": out["chunks"],
        "tools": out["tools"],
        "best_dense_score": out["best_dense_score"],
        "relevant": out["relevant"],
    }

# ---------------------------------------------------------------------------
# Step 3: The refuse node and the generate node
# ---------------------------------------------------------------------------

# The shape of the model's reply. with_structured_output makes the model fill these in,
# so the code gets real values back: no regular expression to read citations or to
# guess whether the reply is a refusal.
class Answer(BaseModel):
    answerable: bool = Field(description="True only if the context contains the answer to the question")
    answer: str = Field(description="The answer, with [n] markers after each claim. If not answerable, one sentence saying so")
    cited: list[int] = Field(description="Numbers of the context chunks cited in the answer")

# The chat model, configured from config.yaml (the same settings the smoke test used)
llm = ChatNebius(
    model_name=cfg["chat"]["model"],
    temperature=gen_cfg["temperature"],
    max_tokens=gen_cfg["max_tokens"],
    nebius_api_base=cfg["nebius"]["api_base"],
    nebius_api_key=os.environ["NEBIUS_API_KEY"],
)
structured_llm = llm.with_structured_output(Answer)

# Turn the retrieved chunks into the numbered context the model sees.
# The numbers are the ones it cites. Each chunk's text already starts with its heading path.
def format_context(chunks):
    return "\n\n".join(
        f'[{i}] source: {c["source"].split("docs/", 1)[1]}\n{c["text"]}'
        for i, c in enumerate(chunks, start=1)
    )

# Refuse node: used when the best dense score is below the threshold.
# A fixed reply and no model call, so it is instant and free.
def refuse_node(state: RagState) -> dict:
    return {"answer": gen_cfg["refusal_message"], "cited": [], "refused": True,
            "sources": [], "citation_issues": []}

# Generate node: ask the model to answer from the numbered chunks.
def generate_node(state: RagState) -> dict:
    messages = [
        SystemMessage(content=gen_cfg["system_prompt"]),
        HumanMessage(content=gen_cfg["user_prompt"].format(
            context=format_context(state["chunks"]), question=state["question"])),
    ]
    # Structured output can occasionally come back empty or malformed, so try twice
    result = None
    for attempt in range(2):
        try:
            result = structured_llm.invoke(messages)
        except Exception as e:
            print("  model call failed:", type(e).__name__)
        if result is not None:
            break
    if result is None:
        raise RuntimeError("The model did not return a valid structured answer after 2 tries")
    # The model can refuse too: when it says the context does not answer the question,
    # return exactly the same reply as the threshold refusal, so both look alike downstream
    if not result.answerable:
        return {"answer": gen_cfg["refusal_message"], "cited": [], "refused": True,
                "sources": [], "citation_issues": []}
    # Step 5: check the citations against the chunks the model was shown (defined below)
    sources, issues = check_citations(result.answer, result.cited, state["chunks"])
    return {"answer": result.answer, "cited": result.cited, "refused": False,
            "sources": sources, "citation_issues": issues}

# ---------------------------------------------------------------------------
# Step 5: Check the citations against the chunks the model was shown
# ---------------------------------------------------------------------------

# Compare the [n] markers in the answer text with the cited list, and with the chunks.
# Returns the cited sources and a list of problems. It never changes the answer.
def check_citations(answer, cited, chunks):
    issues = []
    markers = sorted({int(n) for n in re.findall(r"\[(\d+)\]", answer)})

    # A number outside 1..len(chunks) points to a chunk the model was never shown
    bad = [n for n in markers + list(cited) if not 1 <= n <= len(chunks)]
    if bad:
        issues.append(f"cites chunk numbers that do not exist: {sorted(set(bad))}")

    # The text and the list should name the same chunks
    if markers != sorted(set(cited)):
        issues.append(f"markers in text {markers} differ from the cited list {sorted(set(cited))}")

    # A sentence with no marker is a claim without a citation
    for sentence in re.split(r"(?<=[.!?])\s+", answer.strip()):
        if sentence and not re.search(r"\[\d+\]", sentence):
            issues.append("sentence without a citation: " + sentence[:70])

    # The sources of the valid citations, in the order they were numbered
    sources = [
        {"n": n, "source": chunks[n - 1]["source"].split("docs/", 1)[1], "heading": chunks[n - 1]["heading_path"]}
        for n in markers if 1 <= n <= len(chunks)
    ]
    return sources, issues

# After retrieve, choose the next node: refuse if the docs probably do not cover the
# question (best dense score below the threshold), otherwise generate an answer
def route(state: RagState) -> str:
    return "generate" if state["relevant"] else "refuse"

# The full graph:  retrieve --(relevant?)--> generate --> END
#                           \--(not relevant)--> refuse --> END
def build_graph():
    graph = StateGraph(RagState)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("refuse", refuse_node)
    graph.add_node("generate", generate_node)
    graph.set_entry_point("retrieve")
    # A conditional edge calls route() and follows the node name it returns
    graph.add_conditional_edges("retrieve", route, {"generate": "generate", "refuse": "refuse"})
    graph.add_edge("refuse", END)
    graph.add_edge("generate", END)
    return graph.compile()


if __name__ == "__main__":
    app = build_graph()
    for q in ["How do I set retries on a task?", "What is the Snowflake credit cost?"]:
        result = app.invoke({"question": q})
        print("\n" + q)
        print("  best dense score:", round(result["best_dense_score"], 3), "| relevant:", result["relevant"], "| refused:", result["refused"])
        print("  answer:", result["answer"])
        for s in result["sources"]:
            print(f'  [{s["n"]}] {s["source"]} | {s["heading"][:60]}')
        print("  citation issues:", result["citation_issues"] or "none")
