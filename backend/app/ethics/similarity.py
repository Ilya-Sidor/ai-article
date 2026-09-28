"""Text overlap with the uploaded sources (FR-7.5).

Word 8-gram shingles of every literature chunk are indexed; runs of identical
consecutive words in the manuscript are reported with the matching source
fragment. Text inside quotation marks is excluded (a marked quotation is not
plagiarism).
"""
import re
from collections import defaultdict

N = 8
WARN_WORDS = 12
BLOCK_WORDS = 25
WORD = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*")
QUOTED = re.compile(r"[\"“«][^\"”»]{1,600}[\"”»]")


def _words(text):
    return WORD.findall(text.lower())


def build_index(chunks):
    idx = defaultdict(list)
    words_by_chunk = {}
    for c in chunks:
        w = _words(c["text"])
        words_by_chunk[c["id"]] = w
        for i in range(len(w) - N + 1):
            idx[hash(tuple(w[i:i + N]))].append((c["id"], i))
    return idx, words_by_chunk


def find_runs(text, index, words_by_chunk):
    """Runs of ≥ N identical words: [{start, length, chunk_id, excerpt}]."""
    clean = QUOTED.sub(" § ", text)
    words = _words(clean)
    runs = []
    i = 0
    while i <= len(words) - N:
        hits = index.get(hash(tuple(words[i:i + N])))
        if not hits:
            i += 1
            continue
        best = None
        for cid, j in hits:
            src = words_by_chunk[cid]
            if src[j:j + N] != words[i:i + N]:
                continue
            k = N
            while i + k < len(words) and j + k < len(src) and words[i + k] == src[j + k]:
                k += 1
            if best is None or k > best[1]:
                best = (cid, k)
        if best:
            runs.append({"start": i, "length": best[1], "chunk_id": best[0],
                         "excerpt": " ".join(words[i:i + best[1]])})
            i += best[1]
        else:
            i += 1
    return runs, len(words)


def check(sections, renderer, chunks, labels):
    if not chunks:
        return [], {}
    index, wbc = build_index(chunks)
    by_id = {c["id"]: c for c in chunks}
    issues, stats = [], {}
    for sec in sections:
        text = renderer.text(sec["source"])
        runs, total = find_runs(text, index, wbc)
        matched = sum(r["length"] for r in runs)
        stats[sec["key"]] = {"words": total, "matched": matched,
                             "percent": round(100 * matched / total, 1) if total else 0.0}
        for r in runs:
            if r["length"] < WARN_WORDS:
                continue
            c = by_id[r["chunk_id"]]
            issues.append({
                "section": sec["key"], "heading": sec["heading"],
                "severity": "blocking" if r["length"] >= BLOCK_WORDS else "warning", "code": "overlap",
                "message": f"{r['length']} слов подряд совпадают с {labels.get(c['source_id'], c['source_id'])} "
                           f"(стр. {c.get('page') or '—'}, {c.get('section')})",
                "fragment": r["excerpt"][:240], "chunk_id": r["chunk_id"],
                "suggestion": "перефразируйте или оформите как цитату в кавычках со ссылкой"})
    return issues, stats
