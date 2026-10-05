"""Similar publications: journal suggestions by topic (JANE-like) and already published similar cases.

The model only writes the search queries (from the title, key messages and anonymised case features); the
search itself runs in PubMed and Europe PMC, so every suggested journal and case is a real, indexed record.
Journal suggestions count where articles on the same topic were published in the last 10 years; similar cases
guard a case report against an unfounded "first reported" and give material for the discussion.
"""
import re
from collections import Counter
from datetime import date

from .. import llm
from ..config import MODEL_EXTRACTION
from ..storage import now_iso
from . import metadata as md

EUROPE_PMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "topic_query": {"type": "string"},
        "cases_query": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["topic_query", "cases_query", "keywords"], "additionalProperties": False,
}
QUERY_SYSTEM = """You write PubMed search queries for a pathology manuscript.
topic_query: a PubMed query (MeSH terms and title/abstract words with OR synonyms, combined with AND) that
retrieves original articles on the same entity and question; not too narrow (it should return 50-2000 records).
cases_query: a PubMed query for already published case reports / case series of the same entity with the same
distinctive features (unusual site, morphology, immunophenotype, molecular alteration); do not add the
publication type, it is added automatically.
keywords: 3-6 English search terms. Use English medical terms even if the input is in Russian. Never include
patient details (age, dates, names)."""


def _norm(name):
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def make_queries(project, brief):
    """Queries from the model; without AI — from the keywords and the title."""
    try:
        q = llm._call(project, "discover_queries", MODEL_EXTRACTION, QUERY_SYSTEM, brief, QUERY_SCHEMA,
                      max_tokens=2000, gate=True)
        if q.get("topic_query", "").strip():
            return {k: (v.strip() if isinstance(v, str) else v) for k, v in q.items()}
    except llm.LLMUnavailable:
        pass
    words = re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", brief)[:6]
    q = " AND ".join(dict.fromkeys(words)) or "pathology"
    return {"topic_query": q, "cases_query": q, "keywords": words}


def _summaries(pmids):
    out = []
    for i in range(0, len(pmids), 150):
        out += md.pubmed_summary(pmids[i:i + 150])
    return out


def journals(topic_query, years=10, limit=12):
    since = date.today().year - years
    term = f"({topic_query}) AND (\"{since}\"[dp]:\"3000\"[dp]) NOT (review[pt] OR comment[pt] OR editorial[pt])"
    pmids = md.pubmed_search(term, retmax=200)
    if not pmids:
        return {"query": term, "n": 0, "journals": []}
    counts, examples, issn = Counter(), {}, {}
    for s in _summaries(pmids):
        j = s.get("fulljournalname") or s.get("source")
        if not j:
            continue
        counts[j] += 1
        examples.setdefault(j, []).append({"pmid": s.get("uid"), "title": (s.get("title") or "").rstrip("."),
                                           "year": (s.get("pubdate") or "")[:4]})
        issn.setdefault(j, s.get("issn") or s.get("essn"))
    rows = [{"journal": j, "n": n, "share": round(100 * n / len(pmids)), "issn": issn.get(j),
             "examples": examples[j][:3]} for j, n in counts.most_common(limit)]
    return {"query": term, "n": len(pmids), "journals": rows}


def epmc_query(pubmed_query):
    """PubMed field tags → Europe PMC syntax ("X"[mh] → MESH:"X"; [tiab]/[ti]/[ab] → plain term)."""
    q = re.sub(r'("[^"]+"|[\w-]+)\[(?:mh|mesh|majr|MeSH Terms)(?::noexp)?\]', r"MESH:\1", pubmed_query, flags=re.I)
    q = re.sub(r"\[(?:tiab|ti|ab|tw|all|all fields)\]", "", q, flags=re.I)
    return re.sub(r"\[[^\]]*\]", "", q)


def _europe_pmc(query, size=25):
    r = md.fetch(EUROPE_PMC, params={"query": query, "format": "json", "resultType": "lite", "pageSize": size})
    if r.status_code != 200:
        return []
    return r.json().get("resultList", {}).get("result", [])


def similar_cases(cases_query, limit=25):
    term = f"({cases_query}) AND (case reports[pt] OR case series[tiab])"
    found = {}
    for s in _summaries(md.pubmed_search(term, retmax=limit)):
        found[str(s.get("uid"))] = {"pmid": str(s.get("uid")), "title": (s.get("title") or "").rstrip("."),
                                    "journal": s.get("source"), "year": (s.get("pubdate") or "")[:4],
                                    "authors": ", ".join(a["name"] for a in (s.get("authors") or [])[:3]),
                                    "doi": next((i["value"] for i in s.get("articleids", []) if i.get("idtype") == "doi"),
                                                None), "open_access": None, "source": "PubMed"}
    try:  # Europe PMC: the same records with the open-access flag, plus a few PubMed may rank lower
        for e in _europe_pmc(f"({epmc_query(cases_query)}) AND (PUB_TYPE:\"Case Reports\")", size=limit):
            pmid = e.get("pmid")
            if pmid and pmid in found:
                found[pmid]["open_access"] = e.get("isOpenAccess") == "Y"
            elif pmid and len(found) < limit + 10:
                found[pmid] = {"pmid": pmid, "title": (e.get("title") or "").rstrip("."), "journal": e.get("journalTitle"),
                               "year": e.get("pubYear"), "authors": e.get("authorString", "")[:80], "doi": e.get("doi"),
                               "open_access": e.get("isOpenAccess") == "Y", "source": "Europe PMC"}
    except (md.MetadataError, ValueError):
        pass
    items = sorted(found.values(), key=lambda x: x.get("year") or "", reverse=True)
    return {"query": term, "n": len(items), "cases": items}


def discover(project, brief, queries=None, base_journals=()):
    """queries: the author's edited queries (no AI call then)."""
    q = queries or make_queries(project, brief)
    j = journals(q["topic_query"])
    base = {_norm(p["name"]): p for p in base_journals}
    for row in j["journals"]:
        p = base.get(_norm(row["journal"])) or base.get(_norm(re.sub(r"^the ", "", row["journal"], flags=re.I)))
        row["profile"] = {"id": p["id"], "name": p["name"], "status": p["status"]} if p else None
    c = similar_cases(q["cases_query"])
    return {"queries": q, "journals": j, "cases": c, "at": now_iso()}
