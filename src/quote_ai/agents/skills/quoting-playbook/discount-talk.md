# Talking about a discount (demo data)

The numbers always come from the tools: price_quote gives the list price and, for an asked price, the discount band.
This file is about the words, not the limits. The limits live in config/quote_ai/policy.yml and are enforced by
quote_ai/rules/pricing.py.

1. Restate what the customer asked for: "You'd like $8.00 a box, 7% below our $8.60 list price."
2. Say what the band means, never more:
   - within the rep's limit (up to 5%): "I can offer that."
   - the manager's band (over 5%, up to 15%): "That needs my manager's approval on this exact quote. I'll submit
     it; any change to the quote afterwards needs a new approval."
   - refused (over 15%, or below the margin floor): "I can't go that low. Here is what I can offer instead."
3. Give the value, not a promise: delivery date, verified evidence (chemo testing, latex-free), the alternatives.
4. Never say "approved" until the approval is recorded on the quote version the customer will receive.
