# QuoteAI retrospective (AI-27)

## What worked

- **Contracts first.** The domain models, the state machine and the rules were written and tested before any model
  call; every later episode plugged into them, and the Lakeside fixtures held for 27 episodes.
- **Rules in Python, wrapped everywhere.** One `quote_ai.rules` package runs in pytest, the agent's tools and Unity
  Catalog functions: no second definition of eligibility or price.
- **Truth layers.** Making "unknown" a first-class state stopped every shortcut; Glove B is the proof.
- **The gate as a file.** `v1/thresholds.yml` turned "is it better?" into a reviewed pull request.
- **Isolation by name.** `quote_ai` folders and `quote_ai_` objects kept a shared repo, bundle and catalog workable.

## What didn't, or cost more than expected

- **Numbering drift.** Two `0003` decision records and `adr-win-model-selection.md`; numbers should be assigned when
  a record is opened.
- **Schedules owned twice.** The AI-02 daily ingestion has its own trigger, so the AI-26 daily chain runs an hour
  later; the ingestion config should give up its schedule.
- **Workspace verification debt.** Many adapters say "verify before recording" (alerts API, trace tags, billing tags,
  app OAuth for the smoke test). They are isolated in single functions, but they are untested until a deploy.
- **Prompt and model versions per catalog.** Promotion moves aliases inside a catalog; getting the evaluated versions
  into the prod catalog needs an agreed step.
- **The front end** was never built in this environment; CI builds it.

## What's still missing (honest limits)

Gloves only; synthetic commercial data, so the win chance is a simulation; no ERP integration; not customer-facing;
not for clinical decisions.

## Next

Beyond gloves (masks, syringes, catheters) to show the design scales across device types; the remaining platform
episodes; viewers' questions.
