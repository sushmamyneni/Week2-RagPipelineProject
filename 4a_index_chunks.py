# Stage 4: embed the chunks and upsert them to Pinecone
#
# What this script does:
#   Step 1: Load the settings and read data/chunks.jsonl (written by Stage 3)
#   Step 2: Load the local embedding model and check its vector length
#   Step 3: Ask Pinecone which chunks are already indexed with the same content_hash
#   Step 4: Embed only the missing or changed chunks, in batches
#   Step 5: Upsert them into the index/namespace named in config.yaml, with retries
#   Step 6: Delete vectors whose chunk no longer exists, then verify the count
#
# Usage:
#   python 4a_index_chunks.py                  dry run: reads and reports, writes nothing
#   python 4a_index_chunks.py --limit 128      dry run on the first 128 chunks only (quick timing)
#   python 4a_index_chunks.py --write          real run: writes to Pinecone
#
# Re-running is safe: stable IDs plus the content_hash check mean unchanged chunks are
# skipped, so a second run embeds nothing new.
import json
import sys
from pathlib import Path
import yaml

# ---------------------------------------------------------------------------
# Step 1: Load the settings and the chunks
# ---------------------------------------------------------------------------

# All settings come from config.yaml: paths, embedding model, Pinecone names, batch sizes
cfg = yaml.safe_load(open("config.yaml"))

# The "indexing" section holds batch sizes and retry settings (embed_batch_size,
# upsert_batch_size, fetch_batch_size, max_retries, retry_wait_seconds)
idx = cfg["indexing"]

# Dry run is the default: nothing is written unless you pass --write on the command line
# sys.argv is the list of command-line words, e.g. ["4a_index_chunks.py", "--write"]
write = "--write" in sys.argv

# Read data/chunks.jsonl: one JSON object per line, one line per chunk
# Each chunk has: id, text, tool, version, source, heading_path, content_hash, groups
chunks = [json.loads(line) for line in open(Path(cfg["paths"]["data_dir"]) / "chunks.jsonl", encoding="utf-8")]

# Report what was loaded and which mode is active
print("chunks:", len(chunks), "| mode:", "WRITE" if write else "dry run")

# ---------------------------------------------------------------------------
# Step 2: Load the local embedding model and check its vector length
# ---------------------------------------------------------------------------
from langchain_huggingface import HuggingFaceEmbeddings

emb_cfg = cfg["embedding"]

# The model (BAAI/bge-small-en-v1.5) runs on this machine, so there is no API key,
# no cost and no rate limit. The first load reads the weights from the local cache.
# normalize_embeddings makes every vector length 1, which suits cosine similarity.
embedder = HuggingFaceEmbeddings(
    model_name=emb_cfg["model"],
    encode_kwargs={"normalize_embeddings": True},
)

# embed_documents is the method for chunk text: it takes a list of strings and returns
# a list of vectors, so [0] picks the vector of the first chunk.
# (Questions use embed_query instead, in Stage 5.)
vector = embedder.embed_documents([chunks[0]["text"]])[0]

# The Pinecone index was created with embedding.dimensions, so the model must match it
print("vector length:", len(vector), "| expected:", emb_cfg["dimensions"])

# ---------------------------------------------------------------------------
# Step 3: Ask Pinecone which chunks are already indexed
# ---------------------------------------------------------------------------
import os
from dotenv import load_dotenv
from pinecone import Pinecone

# load_dotenv copies the keys from .env into environment variables. The key is read
# from the environment and never printed.
load_dotenv()
pc_cfg = cfg["pinecone"]
pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
index = pc.Index(pc_cfg["index_name"])
namespace = pc_cfg["namespace"]

# existing maps chunk id -> the content_hash stored with its vector in Pinecone.
# Pinecone is the source of truth, so this stays correct even if you delete the namespace.
# fetch returns only the ids that exist, and this step only reads, so it is safe in a dry run.
existing = {}
for i in range(0, len(chunks), idx["fetch_batch_size"]):
    batch_ids = [c["id"] for c in chunks[i:i + idx["fetch_batch_size"]]]
    res = index.fetch(ids=batch_ids, namespace=namespace)
    for vid, v in res.vectors.items():
        existing[vid] = v.metadata.get("content_hash")

# A chunk needs embedding if it is missing (get returns None), or its text changed
# since it was indexed (the hash differs). Unchanged chunks are skipped.
to_embed = [c for c in chunks if existing.get(c["id"]) != c["content_hash"]]
print("already indexed:", len(existing), "| to embed:", len(to_embed))

# ---------------------------------------------------------------------------
# Step 4: Embed the chunks in batches and build the records to upsert
# ---------------------------------------------------------------------------
import time

# Optional: embed only the first N chunks to time a small run, e.g. --limit 128
if "--limit" in sys.argv:
    to_embed = to_embed[:int(sys.argv[sys.argv.index("--limit") + 1])]

# Each record is what Pinecone stores: the id, the vector, and the metadata
records = []
start = time.perf_counter()
bs = idx["embed_batch_size"]
for i in range(0, len(to_embed), bs):
    batch = to_embed[i:i + bs]
    # One call embeds the whole batch, which is much faster than one chunk at a time
    vectors = embedder.embed_documents([c["text"] for c in batch])
    # zip pairs each chunk with its vector, in order
    for c, vec in zip(batch, vectors):
        records.append({
            "id": c["id"],
            "values": vec,
            # The text goes into the metadata so Stage 5 can read it straight from Pinecone
            "metadata": {k: c[k] for k in ("text", "tool", "version", "source", "heading_path", "content_hash", "groups")},
        })
    # Progress every 10 batches
    if (i // bs) % 10 == 0:
        print(f"embedded {len(records)}/{len(to_embed)}")
print(f"embedded {len(records)} chunks in {time.perf_counter() - start:.0f}s")

# ---------------------------------------------------------------------------
# Step 5: Upsert to Pinecone, with retries
# ---------------------------------------------------------------------------

# Retry only temporary failures. HTTP 429 and 5xx, and network errors that carry no
# status, are worth retrying. Other 4xx errors (bad key, wrong dimension) are permanent,
# so they stop at once. This also covers OpenAI-style "insufficient_quota" 429s: if one
# ever shows up here, it will exhaust the retries and then raise.
# fn is a function with no arguments, so it can be called again on each attempt.
def with_retries(fn):
    for attempt in range(1, idx["max_retries"] + 1):
        try:
            return fn()
        except Exception as e:
            # Pinecone API errors carry the HTTP status, network errors do not
            status = getattr(e, "status", None)
            if status is not None and status < 500 and status != 429:
                raise
            if attempt == idx["max_retries"]:
                raise
            wait = idx["retry_wait_seconds"] * 2 ** (attempt - 1)  # 2s, 4s, 8s, ...
            print(f"  retry {attempt} after {type(e).__name__}, waiting {wait}s")
            time.sleep(wait)

# The only place that writes to Pinecone is behind --write. Upsert means "insert or
# replace by id", so rerunning cannot create duplicates.
if write:
    ub = idx["upsert_batch_size"]
    for i in range(0, len(records), ub):
        batch = records[i:i + ub]
        # The lambda wraps the call so with_retries can run it again
        with_retries(lambda: index.upsert(vectors=batch, namespace=namespace))
        print(f"upserted {min(i + ub, len(records))}/{len(records)}")
else:
    print("dry run: would upsert", len(records), "records to", pc_cfg["index_name"], "/", namespace)

# ---------------------------------------------------------------------------
# Step 6: Remove stale vectors, then verify the count
# ---------------------------------------------------------------------------
chunk_ids = {c["id"] for c in chunks}
if write:
    # index.list yields pages of ids. Anything not in chunks.jsonl is stale, for example
    # after a cleaning change made some chunks disappear.
    stale = [vid for page in index.list(namespace=namespace) for vid in page if vid not in chunk_ids]
    # Pinecone deletes at most 1000 ids per call
    for i in range(0, len(stale), 1000):
        with_retries(lambda: index.delete(ids=stale[i:i + 1000], namespace=namespace))
    print("deleted stale:", len(stale))

    # Pinecone's counts catch up a few seconds after a write (serverless indexes are
    # eventually consistent), so poll for up to 30 seconds
    for _ in range(15):
        ns = index.describe_index_stats().namespaces.get(namespace)
        count = ns.vector_count if ns else 0
        if count == len(chunks):
            break
        time.sleep(2)
    # The Stage 4 check: the vector count must equal the chunk count
    print("vectors in namespace:", count, "| chunks:", len(chunks), "| match:", count == len(chunks))
