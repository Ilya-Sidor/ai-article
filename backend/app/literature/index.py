"""Chunking and lexical retrieval over the project's literature base (FR-3.5).

The prototype uses BM25, which needs no model and no external service. The
interface (``search`` → ranked chunks with source/page/section) is what the
agent and the UI depend on; pgvector hybrid search (PRD 8.1) can replace it.
"""
import math
import re
from collections import Counter

from .parsing import NOT_INDEXED

CHUNK_CHARS = 1100
TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-/][a-z0-9]+)*")
STOPWORDS = set("""a an and are as at be been but by for from had has have he her his in into is it its of on or
our she such that the their them then there these they this those to was were which while who will with within
we not no than also may can could would should all any each both between after before during over under more most
other some only very""".split())
SYNONYMS = {"cd117": ["kit", "c-kit"], "c-kit": ["cd117", "kit"], "ki-67": ["mib-1", "ki67"], "ki67": ["ki-67"],
            "mib-1": ["ki-67"], "gist": ["gastrointestinal", "stromal"], "gists": ["gist"]}

_SENT_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])")


def tokenize(text: str):
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]


def split_sentences(text: str):
    protected = re.sub(r"\b(et al|e\.g|i\.e|Fig|Figs|Tab|vs|approx|No|ca)\.", lambda m: m.group(0).replace(".", "§"),
                       text)
    return [s.replace("§", ".").strip() for s in _SENT_END.split(protected) if s.strip()]


def make_chunks(source_id: str, blocks: list):
    """Group paragraphs of the same page/section into ~CHUNK_CHARS chunks on sentence boundaries."""
    chunks = []
    cur, cur_key = [], None

    def emit():
        if cur:
            text = " ".join(cur).strip()
            chunks.append({"id": f"{source_id}:{len(chunks) + 1:03d}", "source_id": source_id,
                           "page": cur_key[0], "section": cur_key[1], "text": text})
        cur.clear()

    for b in blocks:
        if b["section"] in NOT_INDEXED:
            continue
        key = (b["page"], b["section"])
        if key != cur_key:
            emit()
            cur_key = key
        for sent in split_sentences(b["text"]):
            if cur and sum(len(s) + 1 for s in cur) + len(sent) > CHUNK_CHARS:
                emit()
            cur.append(sent)
    emit()
    return chunks


class BM25:
    def __init__(self, chunks, k1=1.4, b=0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(c["text"] + " " + (c.get("section") or ""))) for c in chunks]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 0
        df = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, top_k: int = 8, source_ids=None):
        terms = tokenize(query)
        expanded = Counter(terms)
        for t in terms:
            for s in SYNONYMS.get(t, []):
                expanded[s] += 0.5
        scored = []
        for i, d in enumerate(self.docs):
            if source_ids and self.chunks[i]["source_id"] not in source_ids:
                continue
            score = 0.0
            for t, w in expanded.items():
                f = d.get(t)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * self.lens[i] / (self.avg or 1))
                score += w * self.idf.get(t, 0) * f * (self.k1 + 1) / denom
            if score > 0:
                scored.append((score, i))
        scored.sort(reverse=True)
        return [dict(self.chunks[i], score=round(s, 3)) for s, i in scored[:top_k]]
