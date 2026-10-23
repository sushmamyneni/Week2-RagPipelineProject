# Stage 3 check: counts per tool, sanity stats, and random chunks from data/chunks.jsonl
# Usage: python 3b_show_chunks.py [number of chunks] [random seed]
import json
import random
import sys
from collections import Counter
from pathlib import Path
import yaml

cfg = yaml.safe_load(open("config.yaml"))
chunks = [json.loads(line) for line in open(Path(cfg["paths"]["data_dir"]) / "chunks.jsonl", encoding="utf-8")]

# Counts per tool
print("chunks per tool:", dict(Counter(c["tool"] for c in chunks)), "total", len(chunks))

# Sanity stats: IDs must be unique, and very short or very long chunks are worth a look
lengths = [len(c["text"]) for c in chunks]
print("unique ids:", len({c["id"] for c in chunks}) == len(chunks))
print("chars min/median/max:", min(lengths), sorted(lengths)[len(lengths) // 2], max(lengths))
print("under 150 chars:", sum(1 for n in lengths if n < 150), "| over 1300 chars:", sum(1 for n in lengths if n > 1300))
print("duplicate texts:", len(chunks) - len({c["content_hash"] for c in chunks}))

# Random chunks to read by eye
n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
random.seed(int(sys.argv[2]) if len(sys.argv) > 2 else None)
for c in random.sample(chunks, n):
    print("\n" + "=" * 80)
    print(c["id"], "|", c["tool"], c["version"], "|", c["source"])
    print("heading:", c["heading_path"], "| chars:", len(c["text"]))
    print("-" * 80)
    print(c["text"])
