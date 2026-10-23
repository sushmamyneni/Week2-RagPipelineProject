# Stage 2: record which docs we index (versions, commits, keep/skip decisions) in data/manifest.json
from pathlib import Path
import fnmatch
import json
import subprocess
import yaml

# Doc file extension for each tool: Airflow is reStructuredText, Spark is Markdown
EXT = {"airflow": ".rst", "spark": ".md"}

# Load all settings (corpus paths, keep/skip rules, versions) from config.yaml
cfg = yaml.safe_load(open("config.yaml"))
corpus_dir = Path(cfg["paths"]["corpus_dir"])

# True if `name` fits any glob pattern in the list, e.g. "ml-guide" vs "ml-*"
def matches(patterns, name):
    return any(fnmatch.fnmatch(name, p) for p in patterns)

# Keep or skip one file. `rel` is its path relative to the docs root.
# A file is checked by its top folder (howto/...) and by its name without extension (tuning.md)
def decide(rel, rules):
    names = {rel.parts[0], rel.stem}
    # Skip wins over keep
    if any(matches(rules["skip"], n) for n in names):
        return "skip"
    if any(matches(rules["keep"], n) for n in names):
        return "keep"
    # Keep is an allow-list: anything that matches neither is skipped
    return "skip"

# Ask git for the exact commit a repo is on (read-only, does not touch the corpus)
def git_sha(repo):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()

manifest = {}

# Build one manifest entry per tool (airflow, spark)
for tool, rules in cfg["corpus"].items():
    root = corpus_dir / rules["root"]
    # All doc files of this tool's type under its docs folder
    files = [f for f in root.rglob("*") if f.is_file() and f.suffix == EXT[tool]]
    # Split files into kept and skipped, storing paths with forward slashes
    kept, skipped = [], []
    for f in files:
        rel = f.relative_to(root)
        if decide(rel, rules) == "keep":
            kept.append(rel.as_posix())
        else:
            skipped.append(rel.as_posix())
    print(tool, "kept", len(kept), "skipped", len(skipped))
    print(kept[:10])
    # Everything needed to reproduce this selection later
    manifest[tool] = {
        "version": rules["version"],
        "commit": git_sha(corpus_dir / tool),
        "root": rules["root"],
        "keep": rules["keep"],
        "skip": rules["skip"],
        "kept_files": kept,
        "skipped_files": skipped,
    }

# Quick check: version, short commit and kept-file count per tool
print({t: (m["version"], m["commit"][:8], len(m["kept_files"])) for t, m in manifest.items()})

# Write the manifest to data/ (git-ignored), creating the folder if needed
data_dir = Path(cfg["paths"]["data_dir"])
data_dir.mkdir(exist_ok=True)
with open(data_dir / "manifest.json", "w") as f:
    json.dump(manifest, f, indent=2)
print("wrote", data_dir / "manifest.json")
