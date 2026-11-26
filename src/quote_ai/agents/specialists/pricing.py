"""The pricing specialist (AI-21): not an LLM. price_quote for the quote's product, the discount band of the price the
customer asked for (approval NONE, MANAGER or REFUSED: pricing.py decides), and the win chance at both prices. It
reads; the Pricing screen's buttons commit through QuoteService."""
from __future__ import annotations

from collections.abc import Iterator

from quote_ai.agents.tools import Toolbox


def plan(deps, quote, rep: str | None = None) -> Iterator[dict]:
    """The same events an agent turn yields (step, tool_result, done), from rules only: no model, no tokens."""
    box = Toolbox(deps, quote, rep, names=["price_quote", "win_probability"])
    product = quote.product_id
    calls = []
    if product:
        target = quote.requirements.target_unit_price
        calls.append(("price_quote", {"product_id": product} | ({"asked_unit_price": target} if target else {})))
    outputs = []
    for name, args in calls:
        yield {"type": "step", "name": name, "args": args, "call_id": f"pricing-{len(outputs)}"}
        out = box.run(name, args)
        outputs.append(out)
        yield {"type": "tool_result", "name": name, "call_id": f"pricing-{len(outputs) - 1}", "output": out,
               "served": "run", "source": out.get("source")}
        if name == "price_quote" and "lines" in out:
            prices = sorted({out["unit_price"]} | ({out["discount_band"]["unit_price"]}
                                                   if "discount_band" in out else set()))
            calls.append(("win_probability", {"product_id": product, "prices": prices}))
    p = next((o for o in outputs if "lines" in o), None)
    if p is None:
        answer = "Nothing to price yet: shortlist a product first." if not product else \
            f"No price: {outputs[0].get('reason')}."
    else:
        answer = f"{product}: ${p['unit_price']:.2f} a box from the price book, ${p['total']:,.2f} in total."
        if "discount_band" in p:
            b = p["discount_band"]
            answer += (f" At the customer's ${b['unit_price']:.2f} ({b['discount_pct']}% off): approval "
                       f"{b['approval']}.")
        w = next((o for o in outputs if "chances" in o), None)
        if w:
            answer += " Win chance: " + ", ".join(f"{round(100 * c['win_chance'])}% at ${c['price']:.2f}"
                                                  for c in w["chances"]) + "."
    yield {"type": "token", "text": answer}
    yield {"type": "done", "answer": answer, "stop_reason": "rules", "violations": [],
           "usage": {"prompt_tokens": 0, "completion_tokens": 0, "model_calls": 0, "seconds": 0.0},
           "facts": {"prices": {product: {"unit_price": p["unit_price"], "total": p["total"],
                                          "price_book_ids": p["price_book_ids"]}} if p else {}},
           "calls": [], "trace_id": None}
