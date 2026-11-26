# Win model selection: a rule written before training

- **Episode:** AI-13, train, track and compare
- **Status:** Accepted, before any model was trained (the commit date of this file is the proof)
- **Owner:** Sales Ops with the data team
- **Code:** `src/quote_ai/ml/train.py` (`select`, `recovers_signal`, `decide`), tested in `tests/quote_ai/test_train.py`

## Context

QuoteAI will show a rep "about 60% chance of winning at this price" (AI-14). Four questions sit behind that number:
does 60% mean 60%, is the model better than a rule a manager could write, why this model and not another, and can we
reproduce it in six months. If we pick the model after looking at the test months, we tune on the test set and the
number stops meaning anything.

The data is synthetic: the quote history of AI-12, with outcomes drawn from a documented, planted signal (price
against the customer's current price, lead time, stock). Every result is an educational simulation on synthetic
held-out months, not evidence about real sales.

## Candidates

1. **Rule baseline** (`quote_ai.ml.baseline`): win if our price is at or below what the customer pays today and we
   deliver within 10 days. Its probability is its bucket's win rate in the training months. It is the bar.
2. **Logistic regression**: simple and explainable, a second baseline.
3. **AutoML's best run**: a fast benchmark (`notebooks/quote_ai/13_automl.py`, 20-minute budget).
4. **Gradient-boosted trees** (scikit-learn HistGradientBoosting): our candidate, lightly tuned, monotonic in price and
   lead time so a higher price never scores a higher chance.

All four are logged in one MLflow experiment, `/Shared/quote_ai/win`, with the training set table and its version as
the dataset input, and the git commit.

## Decision: the selection rule

Measured on the time split of AI-12: train to 2026-04-30, validation 2026-05 to 06, test 2026-07 to 09.

1. Keep a candidate only if its **calibration error on the test months is under 0.05** (expected calibration error,
   10 bins): the rep sees a probability, so it has to mean what it says.
2. Keep it only if its **test AUC beats the rule baseline's test AUC by at least 0.05**.
3. Among those kept, the **highest validation AUC** wins. The test months only decide whether a candidate is allowed,
   never which one is best.
4. If no candidate qualifies, **nothing ships** and the baseline stays.

Accuracy is not used: about 60% of quotes are won, so "always win" would look 60% accurate and be useless.

## The check only a simulation allows

The winner's two top drivers (permutation importance on the validation months) must be features of the planted
signal: `price_vs_customer_current_pct`, `lead_time_days`, `stock_cover_days`. A candidate that wins on the scores but
is driven by something outside the signal (glove colour, say) is refused. With real data we could not know the true
drivers; here we wrote them down in AI-12, so we check.

## Consequences

- The training job (`quote_ai_train`) writes its decision to `quote_ai_ml.model_selection`: the winner's run ID, or
  none with the reason. AI-14 registers only a selected run.
- Win prediction informs the rep; it never sets the price (pricing is a rule: `quote_ai.rules.pricing`, AI-18).
- Changing a threshold is a pull request on this record, reviewed before the next training run, never after.
