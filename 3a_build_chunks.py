# Stage 3: clean the kept docs and split them into chunks (data/chunks.jsonl)
import json
from pathlib import Path
import yaml
import re
import html
import hashlib
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Settings from config.yaml, and Stage 2's output: which files to process
cfg = yaml.safe_load(open("config.yaml"))
corpus_dir = Path(cfg["paths"]["corpus_dir"])
manifest = json.load(open(Path(cfg["paths"]["data_dir"]) / "manifest.json"))

# Turn each ".. list-table::" block into one text line per row
# With a header row: "- Header1: cell; Header2: cell". Without one: "- cell | cell"
def convert_list_tables(text):
    # Directive line, then every deeper-indented or blank line after it (\1 = the directive's indent)
    pattern = re.compile(r"^([ \t]*)\.\. list-table::.*\n((?:[ \t]*\n|\1[ \t]+\S.*\n)*)", re.M)

    def repl(m):
        header_rows = 0
        rows = []
        for line in m.group(2).splitlines():
            s = line.strip()
            if not s:
                continue
            if not rows:
                # Before the first row, ":option:" lines are directive options
                mh = re.match(r":header-rows:\s*(\d+)", s)
                if mh:
                    header_rows = int(mh.group(1))
                if s.startswith(":"):
                    continue
            if s.startswith("* -"):
                rows.append([s[3:].strip()])
            elif s.startswith("-") and rows:
                rows[-1].append(s[1:].strip())
            elif rows:
                # Continuation line of the current cell
                rows[-1][-1] = (rows[-1][-1] + " " + s).strip()
        headers = rows[0] if header_rows and rows else None
        out = []
        for row in rows[header_rows:]:
            if headers:
                out.append("- " + "; ".join(f"{h}: {c}" for h, c in zip(headers, row)))
            else:
                out.append("- " + " | ".join(row))
        # Blank line after, since the pattern swallowed the one that followed the table
        return "\n".join(out) + "\n\n"

    return pattern.sub(repl, text)

# Clean one Airflow .rst file. Rules are added one at a time.
def clean_rst(text):
    # Rule 1: remove the Apache license comment at the top of the file
    # \A anchors at the file start, .*? stops at the first "under the License.",
    # re.S lets . match newlines so the block can span many lines
    text = re.sub(
        r"\A\s*\.\. Licensed to the Apache.*?under the License\.\s*",
        "",
        text,
        count=1,
        flags=re.S,
    )
    # Rule 2: drop image, figure and exampleinclude directives with their :option: lines
    # ^[ \t]* matches the directive at the start of a line, even if indented
    # (?:[ \t]+:.*\n)* then swallows the indented option lines (:language:, :start-after: ...)
    text = re.sub(
        r"^[ \t]*\.\. (?:image|figure|exampleinclude)::.*\n(?:[ \t]+:.*\n)*",
        "",
        text,
        flags=re.M,
    )
    # Rule 3: drop toctree blocks, they are navigation only
    # The directive plus every blank or indented line after it (options and page names)
    text = re.sub(r"^[ \t]*\.\. toctree::.*\n(?:[ \t]*\n|[ \t]+\S.*\n)*", "", text, flags=re.M)
    # Rule 4: drop bare reference labels such as ".. _dag-serialization:"
    # The name may itself contain colons (".. _scheduler:ha:tunables:"), so match up to the final colon
    text = re.sub(r"^\.\. _[^\n]*:[ \t]*\n", "", text, flags=re.M)
    # Rule 5: drop lines that are only a substitution placeholder such as |experimental|
    text = re.sub(r"^\|[^|\n]+\|[ \t]*\n", "", text, flags=re.M)
    # Rule 5b: drop lines that are a lone | (an empty line-block marker)
    text = re.sub(r"^\|[ \t]*\n?", "", text, flags=re.M)
    # Rule 5c: reduce roles to their text, e.g. :meth:`~pkg.Cls.run` -> `pkg.Cls.run`
    # and :ref:`some text <label>` -> `some text`
    def role_text(m):
        inner = m.group(1).lstrip("~!")
        link = re.match(r"^(.*?)\s*<[^>]+>$", inner)
        return "`" + (link.group(1) if link else inner) + "`"
    text = re.sub(r":[a-zA-Z]+(?::[a-zA-Z]+)?:`([^`]+)`", role_text, text)
    # Rule 6: convert list-table blocks to text rows
    text = convert_list_tables(text)
    # Rule 7: collapse runs of blank lines left behind by the removals
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"

# Values for {{site.NAME}} placeholders in the Spark docs, read from the docs' own _config.yml
def spark_site_vars():
    conf = yaml.safe_load(open(corpus_dir / manifest["spark"]["root"] / "_config.yml", encoding="utf-8"))
    return {k: str(v) for k, v in conf.items() if isinstance(v, (str, int, float))}

# Turn each HTML <table> into one text line per row: "- Header: cell; Header: cell"
def html_tables_to_text(text):
    def cell_text(c):
        c = re.sub(r"<br\s*/?>", " ", c)
        c = re.sub(r"<code>(.*?)</code>", r"`\1`", c, flags=re.S)
        c = re.sub(r"<[^>]+>", "", c)
        return re.sub(r"\s+", " ", html.unescape(c)).strip()

    def table_repl(m):
        rows = []
        for tr in re.findall(r"<tr.*?</tr>", m.group(0), flags=re.S):
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S)
            rows.append(("<th" in tr, [cell_text(c) for c in cells]))
        # The first row made of <th> cells holds the column names
        headers = next((cells for is_header, cells in rows if is_header), None)
        out = []
        for is_header, cells in rows:
            if is_header:
                continue
            if headers:
                out.append("- " + "; ".join(f"{h}: {c}" for h, c in zip(headers, cells)))
            else:
                out.append("- " + " | ".join(cells))
        return "\n".join(out) + "\n\n"

    return re.sub(r"<table.*?</table>", table_repl, text, flags=re.S)

# Clean one Spark .md file
def clean_md(text, site_vars):
    # Rule 1: cut the Jekyll front matter, but keep the page title as a heading
    # The title lives only in the front matter, the body has no # heading for it
    title = None
    m = re.match(r"\A---\n(.*?)\n---\n", text, flags=re.S)
    if m:
        front = yaml.safe_load(m.group(1)) or {}
        title = front.get("displayTitle") or front.get("title")
        text = text[m.end():]
    # Rule 2: drop {% comment %} blocks entirely
    text = re.sub(r"\{% comment %\}.*?\{% endcomment %\}", "", text, flags=re.S)
    # Rule 3: drop include lines, the code or tables they pull in are not in the file
    text = re.sub(r"^[ \t]*\{% include(?:_example|_api_gen)? .*?%\}[ \t]*\n", "", text, flags=re.M)
    # Rule 4: turn highlight tags into markdown code fences, keeping the code
    text = re.sub(r"\{% highlight (\w+)[^%]*%\}", r"```\1", text)
    text = re.sub(r"\{% endhighlight %\}", "```", text)
    # Rule 5: drop any other Liquid tag (raw, endraw, ...)
    text = re.sub(r"\{%.*?%\}", "", text)
    # Rule 6: fill in {{site.NAME}} placeholders, leave unknown ones alone
    text = re.sub(r"\{\{\s*site\.(\w+)\s*\}\}", lambda mm: site_vars.get(mm.group(1), mm.group(0)), text)
    # Rule 7: drop table-of-contents markers and the placeholder bullet above {:toc}
    text = re.sub(r"^[*-] This will become a table of contents.*\n", "", text, flags=re.M)
    text = re.sub(r"[ \t]*\{:[^}]*\}", "", text)
    # Rule 8: HTML tables to text rows
    text = html_tables_to_text(text)
    # Rule 9: put the title back as a heading, collapse blank lines
    if title:
        text = f"# {title}\n\n{text}"
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"

# Find headings in cleaned text. Returns (line index, level, title) for each one.
# kind is "md" (# marks) or "rst" (title underlined with ===, ---, ...)
def find_headings(lines, kind):
    found = []
    if kind == "md":
        in_fence = False
        for i, line in enumerate(lines):
            # A # inside a code fence is a code comment, not a heading
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
            m = None if in_fence else re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
            if m:
                # Line 0 is the page title that clean_md put back: level 0, above all body headings
                found.append((i, 0 if i == 0 else len(m.group(1)), m.group(2)))
    else:
        adornment = re.compile(r"([=\-~^\"#*+`:.'_])\1{2,}\s*$")
        levels = {}  # underline character -> level, in order of first appearance
        for i in range(len(lines) - 1):
            title, under = lines[i], lines[i + 1]
            # Headings start at column 0, so indented code never matches
            if not title.strip() or title[0].isspace() or adornment.match(title):
                continue
            if adornment.match(under) and len(under.rstrip()) >= len(title.rstrip()):
                level = levels.setdefault(under[0], len(levels) + 1)
                found.append((i, level, title.strip()))
    return found

# Split cleaned text into sections: [(heading path, body text), ...]
# The heading path is the chain of parent headings, e.g. "Tuning Spark > Memory Management"
def split_sections(text, kind):
    lines = text.splitlines()
    headings = find_headings(lines, kind)
    body_start = 2 if kind == "rst" else 1  # rst heading = title line + underline line
    sections, stack = [], []
    # Text before the first heading belongs to no heading
    first = headings[0][0] if headings else len(lines)
    if "\n".join(lines[:first]).strip():
        sections.append(([], "\n".join(lines[:first]).strip()))
    for n, (i, level, title) in enumerate(headings):
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        end = headings[n + 1][0] if n + 1 < len(headings) else len(lines)
        body = "\n".join(lines[i + body_start:end]).strip()
        sections.append(([t for _, t in stack], body))
    return sections

# (relative path, full path) for each kept file of one tool
# The relative path becomes the chunk's source, the full path is used for reading
def doc_files(tool):
    root = corpus_dir / manifest[tool]["root"]
    return [(rel, root / rel) for rel in manifest[tool]["kept_files"]]

# Doc format per tool: "rst" (Airflow) or "md" (Spark)
KIND = {"airflow": "rst", "spark": "md"}

# Splits a long section into pieces. Tries blank lines first, then line ends, then spaces,
# so table rows and code lines stay whole where possible
ch = cfg["chunking"]
splitter = RecursiveCharacterTextSplitter(
    chunk_size=ch["chunk_size"],
    chunk_overlap=ch["chunk_overlap"],
    separators=["\n\n", "\n", " ", ""],
)

# Clean every kept file, split it into chunks and attach metadata
# Returns the chunk dicts and the files skipped as too short
def build_chunks():
    site_vars = spark_site_vars()
    chunks, skipped_docs = [], []
    for tool, kind in KIND.items():
        for rel, path in doc_files(tool):
            raw = path.read_text(encoding="utf-8")
            cleaned = clean_rst(raw) if kind == "rst" else clean_md(raw, site_vars)
            # Redirect stubs and generated pages are almost empty after cleaning
            if len(cleaned) < ch["min_doc_chars"]:
                skipped_docs.append(f"{tool}/{rel}")
                continue
            source = f"{manifest[tool]['root']}/{rel}".replace("\\", "/")
            n = 0
            for heading, body in split_sections(cleaned, kind):
                # Heading-only sections: their children carry the heading path
                if len(body) < ch["min_section_chars"]:
                    continue
                heading_path = " > ".join(heading)
                for piece in splitter.split_text(body):
                    # The heading path goes first so a short chunk still says where it is from
                    text = f"{heading_path}\n\n{piece}" if heading_path else piece
                    chunks.append({
                        # Stable ID: same file and position give the same ID on every run
                        "id": f"{tool}-{hashlib.sha1(f'{source}|{n}'.encode()).hexdigest()[:16]}",
                        "text": text,
                        "tool": tool,
                        "version": manifest[tool]["version"],
                        "source": source,
                        "heading_path": heading_path,
                        # Changes when the text changes, so Stage 4 knows what to re-embed
                        "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                        "groups": ["public"],
                    })
                    n += 1
    return chunks, skipped_docs

# Build the chunks and write them to data/chunks.jsonl, one JSON object per line
if __name__ == "__main__":
    chunks, skipped_docs = build_chunks()
    out = Path(cfg["paths"]["data_dir"]) / "chunks.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    for tool in KIND:
        print(tool, sum(1 for c in chunks if c["tool"] == tool), "chunks")
    print("skipped as too short:", skipped_docs)
    print("wrote", out)
