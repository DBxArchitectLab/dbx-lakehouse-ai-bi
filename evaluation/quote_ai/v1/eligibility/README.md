# Eligibility suite (AI-15, grown in AI-17)

Zero tolerance. 60 cases (E-001 to E-020 written in AI-15, E-021 to E-060 in AI-17). Each case names a glove, the
request's hard requirements, the **evidence snapshot** it runs against, an expectation for **every** requirement the
request states (M verified match, X verified mismatch, ? unknown) and the status. Every requirement of the contract
(material, powder-free, intended use, latex-free, chemo-tested, sterile, sizes, in distribution) has cases.

Expectations are written from what each glove is and which of its facts are verified (the mock data's design:
`quote_ai.mock.catalog`, `quote_ai.mock.range.truth()`), never from what the engine returns.

Glove B has two cases and both stay: **E-014** on `product_evidence@v41` (chemo claim only in its description ->
unknown -> needs_evidence) and **E-015** on `product_evidence@v57` (its report verified, AI-26 -> eligible). When
Glove B becomes eligible in AI-26, that is E-015 passing, not E-014 failing. Traps: E (latex that looks perfect), M
(discontinued), F (a box of 200; its latex sources disagree).

Snapshots locally: `evaluation/quote_ai/season4.SNAPSHOTS` (the demo evidence each version stands for). In the
workspace a snapshot is a Delta version of the evidence tables.

```
python evaluation/quote_ai/run_eval.py --suite eligibility            # CI, every pull request
python evaluation/quote_ai/run_eval.py --suite eligibility --variant relax_latex   # the demo's break: red
```

**AI-22: elig-007, the Lakeside shortlist case** (`"kind": "shortlist"`). The Lakeside request, with latex-free written
in a footnote, goes through search and the rules as the Shortlist screen builds it; the scorer `no_ineligible_options`
fails the case if a Glove E SKU (latex; GLOVE-E-M is catalog number DBX-NL-M-100) is among the options, or if an
unknown check was counted as a match. In CI it runs on the case's stated requirements; in the `quote_ai_eval` job
(`--endpoint`, the extraction model service) the requirements come from the request text, so a prompt that misses
the footnote puts Glove E on the shortlist and the gate goes red. Glove B's pair (the draft's elig-012a/b) is
E-014 and E-015 above.
