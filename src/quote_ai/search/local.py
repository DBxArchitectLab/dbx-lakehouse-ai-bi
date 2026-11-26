"""An in-memory search index with the AI Search answer shape: tests, the local demo and the evaluation suites.

Not an embedding model. A deterministic stand-in, so the suites run in CI with no workspace:

  vector ("ANN")   cosine over concepts: each word mapped through a small synonym table (CONCEPTS), so
                   "chemotherapy drugs" meets "chemo-tested" and "no latex" meets "latex-free". Like an
                   embedding, it is vague about identifiers: a catalog number is only its pieces.
  keyword          BM25 over the words as written, a catalog number also kept whole: exact terms win.
  HYBRID           reciprocal rank fusion of the two lists (what AI Search's hybrid query does; verify its weighting)
  reranker         re-scores the top results by how many of the query's concepts the row really carries, a
                   stand-in for a reranking model

similarity_search(query_text, columns, filters, num_results, query_type, reranker) returns
{"manifest": {"columns": [{"name": ...}, ..., {"name": "score"}]}, "result": {"data_array": [[...], ...]}}, the
shape databricks-vectorsearch returns, so quote_ai.search.retriever reads both the same way. Filters: {column: value}
or {column: [values]} (IN), applied before ranking, as AI Search does.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping

STOP = {"a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "with", "is", "are", "it", "its", "be", "by",
        "we", "our", "need", "needs", "want", "please", "about", "any", "that", "this", "can", "do", "does", "what",
        "which", "how", "box", "boxes", "glove", "gloves", "size", "sizes", "use", "used", "fine", "ok"}
# word -> concept (a word not listed is its own concept)
CONCEPTS = {
    "chemo": "chemo", "chemotherapy": "chemo", "oncology": "chemo", "cytotoxic": "chemo", "chemo-tested": "chemo",
    "d6978": "chemo", "permeation": "chemo",
    "latex-free": "latex_free", "nrl-free": "latex_free",
    "pf": "powder_free", "powder-free": "powder_free", "powderfree": "powder_free",
    "examination": "exam", "exam": "exam", "patient": "exam",
    "surgical": "surgical", "surgeon": "surgical", "surgery": "surgical", "operating": "surgical",
    "sterile": "sterile", "nitrile": "nitrile", "vinyl": "vinyl", "pvc": "vinyl", "neoprene": "neoprene",
    "chloroprene": "neoprene", "polyisoprene": "polyisoprene", "synthetic": "synthetic",
    "dental": "dental", "dentist": "dental", "thin": "thin", "3": "thin", "mil": "thickness",
    "heavy-duty": "thick", "6": "thick", "thick": "thick", "extended": "long_cuff", "cuff": "long_cuff",
    "12": "long_cuff", "long": "long_cuff", "discontinued": "discontinued", "powdered": "powdered",
}
_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def words(text: str) -> list[str]:
    """Lower-case words; hyphenated words kept whole and also split ("chemo-tested" -> chemo-tested, chemo, tested);
    "no latex" / "latex free" read as latex-free."""
    t = (text or "").lower().replace("no latex", "latex-free").replace("latex free", "latex-free") \
        .replace("powder free", "powder-free").replace("non-latex", "latex-free")
    out = []
    for w in _TOKEN.findall(t):
        out.append(w)
        if "-" in w and not w.startswith("dbx"):
            out += w.split("-")
    return [w for w in out if w not in STOP]


def concepts(text: str) -> Counter:
    c = Counter()
    for w in words(text):
        if re.fullmatch(r"dbx(-[a-z0-9]+)+", w):
            c.update(w.split("-"))            # a catalog number is only its pieces to the "embedding"
            continue
        c[CONCEPTS.get(w, w)] += 1
    return c


def _cosine(a: Counter, b: Counter) -> float:
    """Cosine over concept sets (each concept counts once, so a long text is not rewarded for repeating itself)."""
    if not a or not b:
        return 0.0
    return len(set(a) & set(b)) / math.sqrt(len(a) * len(b))


def _match(row: Mapping, filters: Mapping | None) -> bool:
    for col, want in (filters or {}).items():
        have = row.get(col)
        if isinstance(want, (list, tuple, set)):
            if isinstance(have, (list, tuple)):
                if not set(have) & set(want):
                    return False
            elif have not in want:
                return False
        elif have != want:
            return False
    return True


class InMemoryIndex:
    def __init__(self, rows: Iterable[Mapping], text_column: str = "search_text", primary_key: str = "sku_id",
                 keyword_columns: tuple[str, ...] = ("catalog_number",)) -> None:
        self.rows = [dict(r) for r in rows]
        self.text_column, self.primary_key = text_column, primary_key
        self._docs = []
        for r in self.rows:
            text = " ".join([str(r.get(text_column) or "")] + [str(r.get(c) or "") for c in keyword_columns])
            self._docs.append((Counter(words(text)), concepts(r.get(text_column) or "")))
        self._df = Counter(w for kw, _ in self._docs for w in kw)
        self._avg = sum(sum(kw.values()) for kw, _ in self._docs) / max(len(self._docs), 1)

    def _bm25(self, q: list[str], kw: Counter, k1: float = 1.2, b: float = 0.75) -> float:
        n, dl, s = len(self._docs), sum(kw.values()), 0.0
        for w in set(q):
            if w in kw:
                idf = math.log(1 + (n - self._df[w] + 0.5) / (self._df[w] + 0.5))
                s += idf * kw[w] * (k1 + 1) / (kw[w] + k1 * (1 - b + b * dl / self._avg))
        return s

    def _ranked(self, query: str, idx: list[int], query_type: str,
                keyword_weight: float = 0.5) -> list[tuple[int, float]]:
        """keyword_weight (AI-23): the keyword ranking's share of the fusion; 0.5 is plain reciprocal rank fusion."""
        qc = concepts(query)
        vec = sorted(((i, _cosine(qc, self._docs[i][1])) for i in idx), key=lambda t: (-t[1], t[0]))
        if query_type.upper() != "HYBRID":
            return [t for t in vec if t[1] > 0]
        qw = words(query)
        kw = sorted(((i, self._bm25(qw, self._docs[i][0])) for i in idx), key=lambda t: (-t[1], t[0]))
        fused: Counter = Counter()
        for ranking, w in ((vec, 2 * (1 - keyword_weight)), (kw, 2 * keyword_weight)):
            for rank, (i, score) in enumerate(ranking):
                if score > 0:
                    fused[i] += w / (60 + rank + 1)
        return sorted(fused.items(), key=lambda t: (-t[1], t[0]))

    def _rerank(self, query: str, ranked: list[tuple[int, float]], top: int = 50) -> list[tuple[int, float]]:
        qc = set(concepts(query))
        head = []
        for i, score in ranked[:top]:
            have = set(self._docs[i][1])
            head.append((i, len(qc & have) / max(len(qc), 1) + score))
        return sorted(head, key=lambda t: (-t[1], t[0])) + ranked[top:]

    def similarity_search(self, query_text: str, columns: list[str], filters: Mapping | None = None,
                          num_results: int = 10, query_type: str = "ANN", reranker=None,
                          hybrid_weight: float | None = None, **_) -> dict:
        idx = [i for i, r in enumerate(self.rows) if _match(r, filters)]
        ranked = self._ranked(query_text, idx, query_type, 0.5 if hybrid_weight is None else hybrid_weight)
        if reranker:
            ranked = self._rerank(query_text, ranked)
        data = [[self.rows[i].get(c) for c in columns] + [round(score, 6)] for i, score in ranked[:num_results]]
        return {"manifest": {"columns": [{"name": c} for c in columns] + [{"name": "score"}]},
                "result": {"data_array": data, "row_count": len(data)}}
