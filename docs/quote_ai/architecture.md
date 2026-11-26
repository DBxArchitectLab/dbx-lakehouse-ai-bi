# QuoteAI 1.0: the architecture

QuoteAI is the sales agent of DBxLab Medical Supply (fictional): from a free-text quote request to an issued,
evidence-backed quote, in one app, on one governed platform. Real FDA GUDID device data; synthetic commercial data
(decision record 0007). Sales quoting support only: not customer-facing, not for clinical decisions.

*Rules decide what must be true; AI explains, searches and drafts.* The workflow orchestrates, the agent proposes,
and only `QuoteService` commits a change to a quote.

## The six ideas

1. **The workflow decides; the agent proposes.** Eligibility, fulfilment, pricing and the quote states are rules;
   only QuoteService commits (0004, 0009, 0011, 0012).
2. **Evidence in four kinds of truth.** Source truth, extracted claim, business truth, generated answer; only
   business truth decides, unknown is never a match (0010).
3. **Identity follows the user.** Reps see their own accounts; cost is masked; tools hold only their grants; approval
   is bound to one quote version (0006, 0009).
4. **Quality is a gate.** The evaluation package blocks a worse release; the same scorers run on live traffic
   (AI-22, AI-26).
5. **One app, where the rep works.** React shows; FastAPI calls the services; it never calls a model directly and
   never decides a price.
6. **One platform, governed end to end.** Unity Catalog, Unity Gateway, bundles and GitHub Actions (0001, 0002, AI-25).

## The parts

```
  AccessGUDID (daily)   DBxLab synthetic files                         sales rep / manager
        |                      |                                              |
  [1 Ingestion] AI-02: config-driven, Auto Loader, API source,         [App] quote-ai-<target>
        |       ingestion log           quote_ai_gudid_daily (AI-26)    React screens -> FastAPI /api/*
        v                                                                     |
  [2 Product] silver/gold, SKU model + verified mappings (AI-03)               v
        |                                                              QuoteService (the only writer)
  [3 Evidence] AI Functions, documents, QA sign-off (AI-08, 09)        quote states, approval per version
        |                                                              Lakebase: quotes, events, memory (AI-20)
  [4 Analytics] metric views, dashboard, Genie Agent (AI-10, 11)               ^
        |                                                                     | proposals
  [5 Win model] features, MLflow, UC model @champion, serving (AI-12-14)  [7 Agent] tools as UC functions, MCP,
        |                                                              skills, Agent Runtime (AI-18-21)
  [6 Search + rules] AI Search; eligibility / fulfilment as UC                 ^
        functions; compare with sources (AI-15-17) -------------------------+
                                                                              |
  Unity Gateway: every model call (extract, chat, embed, judge), limits, guardrails (AI-06, AI-24)
  [8 Engineering, alongside] evaluation gate (AI-22), optimization (AI-23), security (AI-24),
        release manifest + CI/CD (AI-25), freshness, live scoring, cost, Ops page (AI-26), capstone (AI-27)
```

## The Lakeside transaction on Databricks

| Step | Lakeside | Databricks | Episode |
| --- | --- | --- | --- |
| Request -> r1 | The email; what is missing | Foundation Model APIs through Unity Gateway, Prompt Registry, structured output | 05, 06 |
| Clarify -> r2 | S 60, M 140, L 140, XL 60, boxes of 100 | Confirm requirements screen; QuoteService | 07 |
| Quote lines | One per size, on verified SKUs | Delta, Unity Catalog; mapping review | 03, 07 |
| Eligibility | A, B, C, D eligible; E excluded (latex) | AI Search candidates; `check_eligibility` UC function | 15 |
| Fulfilment | A, D in full; C partial: an alternative | `check_fulfilment` UC function | 15 |
| Compare | Verified specs with document versions | RAG with citations; Knowledge Assistant compared | 16, 17 |
| History | What Lakeside paid | Metric views, Genie Agent as the rep | 10, 11, 24 |
| Win chance | Three prices (a simulation) | UC model @champion on Model Serving | 12-14 |
| Price | $8.60 list; $8.00 asked = 7% | `price_quote` UC function, policy.yml bands | 18, 21 |
| Approval | Manager approves version 2 | Approval inbox; terms hash | 21 |
| Issue | Fresh inputs; ISSUED | QuoteService, freshness policy, Lakebase | 20, 21, 26 |

## Where things are

`README_QuoteAI.md` (Layout) lists every file; `docs/quote_ai/decisions/` the twelve decision records;
`docs/quote_ai/deployment-guide.md` the order to build it from an empty workspace; `docs/quote_ai/runbooks/` release,
rollback and incidents; `evaluation/quote_ai/capstone/` the scorecard against the plan's success criteria.

## Known limits

Gloves only. Synthetic commercial data, so the win chance is an educational simulation. No ERP integration. Not
customer-facing. Not for clinical decisions. See `docs/quote_ai/retrospective.md`.
