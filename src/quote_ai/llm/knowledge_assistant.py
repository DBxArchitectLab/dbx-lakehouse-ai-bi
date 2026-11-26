"""The "buy" option (AI-16): Agent Bricks Knowledge Assistant, configured on the same supplier documents and playbook.

How it is created: resources/quote_ai/quote_ai_knowledge_assistant.notes.md (UI or API; not a bundle resource).
It is asked through its serving endpoint with the OpenAI-compatible Responses API, and its answer is turned into the
same statements the build chain returns, so the Comparison suite scores both the same way:

  ask_ka          one question -> the raw response (text and cited sources)
  to_statements   the response -> [{text, citation, layer}]: each sentence with the source cited after it; the layer
                  from the knowledge chunks of that document (business truth only for a verified supplier document);
                  a sentence with no source gets no citation, and the suite counts it

Verify before recording: availability in the Azure region; the endpoint name the Knowledge Assistant gets; the
response shape (output[].content[].text and annotations with a file name or URL per citation) and whether the
Responses API or chat completions is the supported call; whether a per-account filter is possible (it is not in
this code: the KA sees every document it was given, see docs/quote_ai/decisions/0003-rag-build-or-buy.md).
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

ENDPOINT = "quote_ai_knowledge_assistant"


def ask_ka(question: str, endpoint: str = ENDPOINT, client: Any = None) -> Any:
    if client is None:
        from quote_ai.llm.client import workspace_client
        client = workspace_client()
    return client.responses.create(model=endpoint, input=[{"role": "user", "content": question}])


def _parts(response: Any) -> list[tuple[str, list[str]]]:
    """(text, cited sources) per output text part, tolerant of dicts and SDK objects."""
    def get(o, k, default=None):
        return o.get(k, default) if isinstance(o, dict) else getattr(o, k, default)
    out = []
    for item in get(response, "output", []) or []:
        for c in get(item, "content", []) or []:
            text = get(c, "text")
            if not text:
                continue
            sources = []
            for a in get(c, "annotations", []) or []:
                src = get(a, "filename") or get(a, "file_id") or get(a, "url") or get(a, "title")
                if src:
                    sources.append(str(src))
            out.append((text, sources))
    return out


def doc_id_of(source: str) -> str:
    """A cited file (/Volumes/.../test_reports/RPT-CHEMO-A_v1.pdf) -> its document ID (RPT-CHEMO-A)."""
    stem = PurePosixPath(source.split("?")[0]).stem
    return re.sub(r"_v\d+$", "", stem)


def to_statements(response: Any, chunks: list[dict]) -> list[dict]:
    """The KA's answer as statements. A sentence is cited by the first source of its part; the citation is a chunk
    of that document (the first section chunk), so the truth layer comes from our data, never from the KA."""
    by_doc: dict[str, dict] = {}
    for c in chunks:
        if c.get("chunking", "section") == "section":
            by_doc.setdefault(c["doc_id"], c)
    out = []
    for text, sources in _parts(response):
        chunk = next((by_doc[doc_id_of(s)] for s in sources if doc_id_of(s) in by_doc), None)
        for sentence in [x.strip() for x in re.split(r"(?<=[.!?])\s+", text) if x.strip()]:
            out.append({"text": sentence, "citation": chunk["chunk_id"] if chunk else "",
                        "layer": chunk["truth_layer"] if chunk else ""})
    return out
