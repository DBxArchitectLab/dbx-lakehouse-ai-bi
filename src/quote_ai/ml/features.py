"""From quote history to ML features (AI-12): the label, the feature tables, the point-in-time training set, the time
split, and the checks that refuse a leak.

The label (ADR 0003, the same as the win-rate metric of AI-10): won = 1; lost or expired = 0; cancelled and open
quotes are left out.

Features, all known when the quote was sent (sent_at):
  price      price_vs_list_pct, price_vs_customer_current_pct
  delivery   lead_time_days, stock_cover_days                     (inventory snapshot at sent_at)
  account    orders_12m, win_rate_12m, days_since_last_order       (history before sent_at)
  product    material, chemo_verified, use_type                    (verified evidence only, as of sent_at)
  context    quantity_boxes, needs_approval                        (the quote as sent)
Excluded on purpose: final_negotiated_price and days_to_decision, known only after the outcome.

Three feature tables in quote_ai_ml, each with a primary key and a timestamp key:
  account_features        (account_id, as_of)  one row per account per day, and one per current-price observation
  sku_features            (sku_id, as_of)      one row per inventory snapshot, and one per evidence verification
  quote_context_features  (quote_id, sent_at)  the quote's own values, from request_features()
plus one on-demand feature, price_vs_customer_current_pct: a Unity Catalog Python function (ml_tables.sql) that
computes it from the proposed price and the looked-up current price, in training and in serving alike.

The pure functions below build the same rows as the Spark side, so the point-in-time rules are unit tested
(tests/quote_ai/test_features.py) and the feature job cross-checks Feature Engineering's training set against them.
Spark and databricks-feature-engineering are imported lazily: only the job needs them.
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta, timezone

from quote_ai.rules.policy import PricingPolicy

LABEL = "won"
DECIDED = ("WON", "LOST", "EXPIRED")        # ADR 0003: the win-rate denominator; expired counts as lost
KEYS = ("quote_id", "account_id", "sku_id", "sent_at")

ACCOUNT_FEATURES = ("orders_12m", "win_rate_12m", "days_since_last_order", "customer_current_price")
SKU_FEATURES = ("lead_time_days", "stock_cover_days", "material", "use_type", "chemo_verified")
CONTEXT_FEATURES = ("unit_price", "list_price", "quantity_boxes", "price_vs_list_pct", "needs_approval")
ON_DEMAND = ("price_vs_customer_current_pct",)
# What the model sees: the draft's feature list. Raw inputs (prices) and keys are used to compute or look up features,
# then dropped.
FEATURES = ("price_vs_list_pct", "price_vs_customer_current_pct", "lead_time_days", "stock_cover_days", "orders_12m",
            "win_rate_12m", "days_since_last_order", "material", "chemo_verified", "use_type", "quantity_boxes",
            "needs_approval")
NOT_MODEL_INPUTS = ("quote_id", "account_id", "sku_id", "sent_at", "unit_price", "list_price",
                    "customer_current_price")
EXCLUDED = ("final_negotiated_price", "days_to_decision")   # known only after the outcome: never a feature

# The time split (train on the past, predict the future)
TRAIN_END = date(2026, 4, 30)
VALID_END = date(2026, 6, 30)
TEST_END = date(2026, 9, 30)

WINDOW = timedelta(days=365)
STOCK_COVER_DAILY_BOXES = 40        # average boxes a SKU sells per day (demo constant; from order_line at scale)


class LeakageError(AssertionError):
    """A training set that would let the model read the answer."""


# ------------------------------------------------------------------ small helpers

def utc(ts) -> datetime:
    """A timestamp as an aware UTC datetime (Spark hands back naive ones in the session time zone, UTC on jobs)."""
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, date):
        return datetime.combine(ts, time(), tzinfo=timezone.utc)
    raise TypeError(f"not a timestamp: {ts!r}")


def pct_diff(price: float | None, reference: float | None) -> float | None:
    """(price / reference - 1) in percent, 2 decimals: -5.0 is 5% below. The same expression as the Unity Catalog
    function {ml}.price_vs_customer_current_pct (ml_tables.sql); a test runs both."""
    if price is None or not reference:
        return None
    return round((price / reference - 1) * 100, 2)


def request_features(unit_price: float, list_price: float, quantity_boxes: int,
                     policy: PricingPolicy | None = None) -> dict:
    """The quote's own features, for a past quote (the feature job) and for a proposed price (scoring, the app and,
    from AI-18, the agent): one definition, so training and serving can't drift apart. needs_approval follows the
    policy's rep limit exactly as the sales history recorded it."""
    limit = (policy or PricingPolicy()).rep_discount_limit
    discount = round(1 - unit_price / list_price, 4)
    return {"unit_price": float(unit_price), "list_price": float(list_price), "quantity_boxes": int(quantity_boxes),
            "price_vs_list_pct": pct_diff(unit_price, list_price), "needs_approval": discount > limit + 1e-9}


class AsOf:
    """Point-in-time lookup: for a key and a timestamp, the latest row whose time is at or before it. Never after."""

    def __init__(self, rows: Iterable[Mapping], key: str, ts: str) -> None:
        self.key, self.ts = key, ts
        grouped: dict = defaultdict(list)
        for r in rows:
            grouped[r[key]].append(r)
        self._rows = {k: sorted(v, key=lambda r: utc(r[ts])) for k, v in grouped.items()}
        self._times = {k: [utc(r[ts]) for r in v] for k, v in self._rows.items()}

    def get(self, key, at) -> Mapping | None:
        times = self._times.get(key)
        if not times:
            return None
        i = bisect.bisect_right(times, utc(at))
        return self._rows[key][i - 1] if i else None

    def latest(self, key) -> Mapping | None:
        """What a plain join on the key would give: today's row. Used only to show the leak."""
        rows = self._rows.get(key)
        return rows[-1] if rows else None


# ------------------------------------------------------------------ label and the quote's own context

def label_rows(quotes: Iterable[Mapping]) -> list[dict]:
    """quote_ai_ml.quote_labels from quote_ai_sales.quote_fact: decided quotes that were sent. Cancelled and open
    quotes are left out, as in the win-rate metric."""
    out = []
    for q in quotes:
        if q.get("outcome") in DECIDED and q.get("issued_at") is not None:
            out.append({"quote_id": q["quote_id"], "account_id": q["account_id"], "sku_id": q["sku_id"],
                        "sent_at": utc(q["issued_at"]), LABEL: int(q["outcome"] == "WON")})
    return sorted(out, key=lambda r: r["sent_at"])


def quote_context_rows(quotes: Iterable[Mapping], policy: PricingPolicy | None = None) -> list[dict]:
    """quote_ai_ml.quote_context_features: each sent quote's own values (the quote as it was sent; it never changes)."""
    out = []
    for q in quotes:
        if q.get("issued_at") is None:
            continue
        out.append({"quote_id": q["quote_id"], "sent_at": utc(q["issued_at"])}
                   | request_features(float(q["unit_price"]), float(q["list_price"]), int(q["quantity"]), policy))
    return out


# ------------------------------------------------------------------ account features, point in time

def account_feature_rows(quotes: Iterable[Mapping], orders: Iterable[Mapping], prices: Iterable[Mapping],
                         start: date, end: date) -> list[dict]:
    """quote_ai_ml.account_features: for each account, one row per day from start to end (at midnight UTC) and one at
    each current-price observation. Each row holds what was known at its as_of, strictly from earlier events:
      win_rate_12m           quotes DECIDED in the 12 months before as_of (a quote sent earlier but decided later
                             is not known yet); same definition as the metric
      orders_12m             order lines in the 12 months before as_of (an order is known the day after its date)
      days_since_last_order  days from the last known order to as_of
      customer_current_price the latest observation at or before as_of
    """
    decided: dict = defaultdict(list)          # account -> [(decided_at, won)]
    for q in quotes:
        if q.get("outcome") in DECIDED and q.get("decided_at") is not None:
            decided[q["account_id"]].append((utc(q["decided_at"]), q["outcome"] == "WON"))
    known_orders: dict = defaultdict(list)     # account -> [known_at]
    for o in orders:
        known_orders[o["account_id"]].append(utc(o["order_date"]) + timedelta(days=1))
    obs = AsOf(prices, "account_id", "observed_at")
    price_times: dict = defaultdict(list)
    for p in prices:
        price_times[p["account_id"]].append(utc(p["observed_at"]))

    out = []
    for account in sorted(set(decided) | set(known_orders) | set(price_times)):
        d = sorted(decided[account])
        d_times = [t for t, _ in d]
        wins = [0]
        for _, w in d:
            wins.append(wins[-1] + w)
        o_times = sorted(known_orders[account])
        stamps = {utc(start) + timedelta(days=i) for i in range((end - start).days + 1)} | set(price_times[account])
        for t in sorted(stamps):
            lo, hi = bisect.bisect_left(d_times, t - WINDOW), bisect.bisect_left(d_times, t)
            n = hi - lo
            o_lo, o_hi = bisect.bisect_left(o_times, t - WINDOW), bisect.bisect_right(o_times, t)
            last = o_times[o_hi - 1] if o_hi else None
            p = obs.get(account, t)
            out.append({"account_id": account, "as_of": t, "orders_12m": o_hi - o_lo,
                        "win_rate_12m": round((wins[hi] - wins[lo]) / n, 4) if n else None,
                        "days_since_last_order": (t - last).days if last else None,
                        "customer_current_price": float(p["current_price"]) if p else None})
    return out


# ------------------------------------------------------------------ SKU features, point in time

def sku_feature_rows(inventory: Iterable[Mapping], skus: Iterable[Mapping],
                     evidence: Iterable[Mapping] = ()) -> list[dict]:
    """quote_ai_ml.sku_features: one row per inventory snapshot and per verification of the SKU's chemo evidence.
    material and use_type come from the SKU model; chemo_verified is true only once a verified chemo_tested = true
    row exists (verified_at at or before as_of): AI-extracted claims never count."""
    snaps = AsOf(inventory, "sku_id", "as_of")
    sku_info = {s["sku_id"]: s for s in skus}
    verified: dict = defaultdict(list)         # product_id or sku_id -> [verified_at]
    for e in evidence:
        if (e.get("attribute_name") == "chemo_tested" and str(e.get("attribute_value")).lower() == "true"
                and e.get("verification_status") == "verified" and e.get("verified_at") is not None):
            verified[e.get("sku_id") or e.get("product_id")].append(utc(e["verified_at"]))
    times: dict = defaultdict(set)
    for r in inventory:
        times[r["sku_id"]].add(utc(r["as_of"]))
    out = []
    for sku_id, info in sorted(sku_info.items()):
        v_times = sorted(verified.get(sku_id, []) + verified.get(info.get("product_id"), []))
        for t in sorted(times[sku_id] | set(v_times)):
            snap = snaps.get(sku_id, t)
            if snap is None:
                continue
            out.append({"sku_id": sku_id, "as_of": t, "lead_time_days": int(snap["lead_time_days"]),
                        "stock_cover_days": round(snap["available"] / STOCK_COVER_DAILY_BOXES, 1),
                        "material": info.get("material"), "use_type": info.get("intended_use"),
                        "chemo_verified": bool(v_times) and v_times[0] <= t})
    return out


# ------------------------------------------------------------------ the training set

def _row(label: Mapping, context: Mapping, account: Mapping | None, sku: Mapping | None) -> dict:
    row = dict(label)
    row.update({k: context[k] for k in CONTEXT_FEATURES})
    row.update({k: (account or {}).get(k) for k in ACCOUNT_FEATURES})
    row.update({k: (sku or {}).get(k) for k in SKU_FEATURES})
    row["price_vs_customer_current_pct"] = pct_diff(row["unit_price"], row["customer_current_price"])
    row["_account_as_of"] = account["as_of"] if account else None
    row["_sku_as_of"] = sku["as_of"] if sku else None
    return row


def training_rows(labels: Iterable[Mapping], context: Iterable[Mapping], account_rows: Iterable[Mapping],
                  sku_rows: Iterable[Mapping]) -> list[dict]:
    """The point-in-time training set, as Feature Engineering's create_training_set builds it: each label joined to
    its own quote context, and to the latest account and SKU feature rows at or before sent_at. Each row also keeps
    the as_of it used (_account_as_of, _sku_as_of), so check_training_set can prove nothing came from the future."""
    ctx = {c["quote_id"]: c for c in context}
    acc, sku = AsOf(account_rows, "account_id", "as_of"), AsOf(sku_rows, "sku_id", "as_of")
    return [_row(l, ctx[l["quote_id"]], acc.get(l["account_id"], l["sent_at"]), sku.get(l["sku_id"], l["sent_at"]))
            for l in labels]


def plain_join_rows(labels: Iterable[Mapping], context: Iterable[Mapping], account_rows: Iterable[Mapping],
                    sku_rows: Iterable[Mapping], history: Iterable[Mapping]) -> list[dict]:
    """The easy way, kept only to show the leak (AI-12's demo and tests): a plain join on account_id and sku_id gives
    every old quote today's values, and final_negotiated_price and days_to_decision ride along from the history."""
    ctx = {c["quote_id"]: c for c in context}
    after = {h["quote_id"]: h for h in history}
    acc, sku = AsOf(account_rows, "account_id", "as_of"), AsOf(sku_rows, "sku_id", "as_of")
    out = []
    for l in labels:
        row = _row(l, ctx[l["quote_id"]], acc.latest(l["account_id"]), sku.latest(l["sku_id"]))
        h = after.get(l["quote_id"], {})
        row.update(final_negotiated_price=h.get("final_negotiated_price"), days_to_decision=h.get("days_to_decision"))
        out.append(row)
    return out


def split_of(sent_at) -> str | None:
    """train up to TRAIN_END, valid up to VALID_END, test up to TEST_END (inclusive days); later quotes: none yet."""
    d = utc(sent_at).date()
    if d <= TRAIN_END:
        return "train"
    if d <= VALID_END:
        return "valid"
    if d <= TEST_END:
        return "test"
    return None


def time_split(rows: Iterable[Mapping]) -> tuple[list[dict], list[dict], list[dict]]:
    """Train on older months, validate and test on later ones: how the model will be used."""
    parts: dict[str, list[dict]] = {"train": [], "valid": [], "test": []}
    for r in rows:
        s = split_of(r["sent_at"])
        if s:
            parts[s].append(dict(r) | {"split": s})
    return parts["train"], parts["valid"], parts["test"]


def check_training_set(rows: Iterable[Mapping], splits: Mapping[str, Iterable[Mapping]] | None = None) -> list[str]:
    """Every problem that would let a model read the answer; empty means clean. One assertion per row:
    no feature row from after sent_at, no excluded column, a 0/1 label; and no quote in two splits."""
    problems = []
    for r in rows:
        for col in ("_account_as_of", "_sku_as_of"):
            if r.get(col) is not None and utc(r[col]) > utc(r["sent_at"]):
                problems.append(f"{r['quote_id']}: {col} {r[col]} is after sent_at {r['sent_at']}")
        for col in EXCLUDED:
            if col in r:
                problems.append(f"{r['quote_id']}: {col} is known only after the outcome")
        if r.get(LABEL) not in (0, 1):
            problems.append(f"{r['quote_id']}: label {r.get(LABEL)!r} is not 0 or 1")
    if splits:
        seen: dict[str, str] = {}
        for name, part in splits.items():
            for r in part:
                if r["quote_id"] in seen and seen[r["quote_id"]] != name:
                    problems.append(f"{r['quote_id']}: in {seen[r['quote_id']]} and {name}")
                seen[r["quote_id"]] = name
        ends = {n: max((utc(r["sent_at"]) for r in p), default=None) for n, p in splits.items()}
        starts = {n: min((utc(r["sent_at"]) for r in p), default=None) for n, p in splits.items()}
        for a, b in (("train", "valid"), ("valid", "test"), ("train", "test")):
            if ends.get(a) and starts.get(b) and ends[a] >= starts[b]:
                problems.append(f"{a} reaches {ends[a]}, after {b} starts at {starts[b]}")
    return problems


def assert_training_set(rows, splits=None) -> None:
    problems = check_training_set(rows, splits)
    if problems:
        raise LeakageError(f"{len(problems)} leak(s), first: {problems[0]}")


def model_input(row: Mapping) -> dict:
    """Only what the model sees."""
    return {k: row.get(k) for k in FEATURES}


def build(facts: list[dict], orders: list[dict], prices: list[dict], inventory: list[dict], skus: list[dict],
          evidence: Iterable[Mapping] = (), end: date | None = None) -> dict[str, list[dict]]:
    """Every table of AI-12 from source rows, in memory: the feature job's logic for tests and the demo notebook."""
    sent = [utc(f["issued_at"]) for f in facts if f.get("issued_at") is not None]
    start = min(sent).date() - timedelta(days=1)
    end = end or max(utc(p["observed_at"]) for p in prices).date()
    labels = label_rows(facts)
    context = quote_context_rows(facts)
    accounts = account_feature_rows(facts, orders, prices, start, end)
    sku_rows = sku_feature_rows(inventory, skus, evidence)
    return {"quote_labels": labels, "quote_context_features": context, "account_features": accounts,
            "sku_features": sku_rows, "training": training_rows(labels, context, accounts, sku_rows)}


# ------------------------------------------------------------------ Databricks: Feature Engineering in Unity Catalog

def feature_lookups(names: Mapping[str, str]) -> list:
    """The point-in-time lookups and the on-demand function. Verify before recording: FeatureLookup's
    timestamp_lookup_key, FeatureFunction's input_bindings, and that both work on serverless job compute."""
    from databricks.feature_engineering import FeatureFunction, FeatureLookup
    ml = names["ml"]
    return [
        FeatureLookup(table_name=f"{ml}.account_features", lookup_key="account_id", timestamp_lookup_key="sent_at",
                      feature_names=list(ACCOUNT_FEATURES)),
        FeatureLookup(table_name=f"{ml}.sku_features", lookup_key="sku_id", timestamp_lookup_key="sent_at",
                      feature_names=list(SKU_FEATURES)),
        FeatureFunction(udf_name=f"{ml}.price_vs_customer_current_pct", output_name="price_vs_customer_current_pct",
                        input_bindings={"unit_price": "unit_price", "customer_current_price": "customer_current_price"}),
    ]


def create_training_set(fe, labels_with_context, names: Mapping[str, str], label: str | None = LABEL,
                        exclude: Iterable[str] = NOT_MODEL_INPUTS):
    """fe.create_training_set over quote_labels joined to quote_context_features (the quote's own values): account
    and SKU features as of sent_at, the on-demand price feature. By default only the model's inputs are kept: that
    object is passed to fe.log_model in AI-13, so the served model looks up the same features (AI-14). The feature
    job keeps the keys (exclude=RAW_INPUTS) to write win_training_set with its split."""
    return fe.create_training_set(df=labels_with_context, feature_lookups=feature_lookups(names), label=label,
                                  exclude_columns=list(exclude))


RAW_INPUTS = ("unit_price", "list_price", "customer_current_price")


def write_feature_table(fe, name: str, df) -> None:
    """Merge rows into a feature table created by ml_tables.sql (primary key + TIMESERIES key, comments, owner).
    Verify: write_table's merge mode on a table with a TIMESERIES primary key."""
    fe.write_table(name=name, df=df, mode="merge")


def compare(pure: Iterable[Mapping], spark_rows: Iterable[Mapping], columns: Iterable[str],
            key: str = "quote_id") -> list[str]:
    """Where Feature Engineering's training set and the pure builder disagree (the job fails on any): a second,
    independent check that the lookups are point in time."""
    theirs = {r[key]: r for r in spark_rows}
    out = []
    for r in pure:
        other = theirs.get(r[key])
        if other is None:
            out.append(f"{r[key]}: missing from the training set")
            continue
        for c in columns:
            a, b = r.get(c), other.get(c)
            if isinstance(a, float) or isinstance(b, float):
                same = a is not None and b is not None and abs(float(a) - float(b)) < 1e-6 or a is None and b is None
            else:
                same = a == b
            if not same:
                out.append(f"{r[key]}: {c} {a!r} != {b!r}")
    return out
