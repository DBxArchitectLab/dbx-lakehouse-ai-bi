import { useState } from "react";
import { api, ApiError, newKey, postWithKey, type PricePreview, type Quote } from "../api";

// Pricing (AI-21, QuoteAI v3), for reps. The price is price_quote's, from the price book (AI-18); the band of an asked
// price (none, a manager, refused) is pricing.py's answer, shown before anything is written. Refused has no
// approval button. Submit and Issue send one idempotency key per click; the back end decides everything.
export default function Pricing({ quote, onChange }: { quote: Quote; onChange: (q: Quote) => void }) {
  const eligible = (quote.candidates ?? []).filter((c) => c.status === "eligible");
  const [product, setProduct] = useState<string>(quote.product_id ?? eligible.find((c) => c.on_time)?.product_id ?? "");
  const [asked, setAsked] = useState<string>("");
  const [preview, setPreview] = useState<PricePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = (p: Promise<Quote>) => {
    setBusy(true);
    setError(null);
    p.then(onChange)
      .catch((e: ApiError) => setError(e.detail))
      .finally(() => setBusy(false));
  };
  const check = () => {
    setError(null);
    const q = new URLSearchParams();
    if (product && product !== quote.product_id) q.set("product_id", product);
    if (asked) q.set("asked", asked);
    api
      .get<PricePreview>(`/api/price/${quote.quote_id}?${q}`)
      .then(setPreview)
      .catch((e: ApiError) => setError(e.detail));
  };
  const price = () =>
    run(
      api.post<Quote>(`/api/price/${quote.quote_id}`, {
        revision: quote.revision,
        product_id: product || null,
        asked_unit_price: asked ? Number(asked) : null,
      }),
    );
  const submit = () => run(postWithKey<Quote>(`/api/price/${quote.quote_id}/submit`, { revision: quote.revision }, newKey()));
  const issue = () => run(postWithKey<Quote>(`/api/price/${quote.quote_id}/issue`, { revision: quote.revision }, newKey()));
  const band = preview?.band;

  return (
    <section>
      <h2>
        Pricing {quote.quote_id} · v{quote.version} · <span className="state">{quote.state}</span>
      </h2>
      <label>
        Product
        <select value={product} onChange={(e) => setProduct(e.target.value)}>
          <option value="">choose an eligible product</option>
          {eligible.map((c) => (
            <option key={c.product_id} value={c.product_id}>
              {c.product_id} · {c.label ?? ""}
            </option>
          ))}
        </select>
      </label>
      <label>
        Price the customer asked for (optional)
        <input className="price" value={asked} inputMode="decimal" onChange={(e) => setAsked(e.target.value)} />
      </label>
      <button onClick={check} disabled={!product && !quote.product_id}>Check the price</button>
      {preview && (
        <table>
          <thead>
            <tr><th>Size</th><th>SKU</th><th>Boxes</th><th>Price book</th><th>Row</th></tr>
          </thead>
          <tbody>
            {preview.lines.map((l) => (
              <tr key={l.size}>
                <td>{l.size}</td>
                <td>{l.sku_id}</td>
                <td>{l.boxes}</td>
                <td>${l.unit_price.toFixed(2)}</td>
                <td className="muted">{l.price_book_id}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {band && (
        <p className="banner">
          ${band.unit_price.toFixed(2)} is {band.discount_pct}% off ${band.list_price.toFixed(2)}, {band.margin_pct === null ? "margin: managers only" : `margin ${band.margin_pct}%`}:{" "}
          {band.approval === "NONE" && "within the rep's limit, no approval needed."}
          {band.approval === "MANAGER" && "a sales manager must approve this quote version."}
          {band.approval === "REFUSED" && "refused by the pricing policy. There is no approval for it."}
        </p>
      )}
      <p>
        <button onClick={price} disabled={busy || band?.approval === "REFUSED" || !(product || quote.product_id)}>
          Price this version
        </button>{" "}
        <button onClick={submit} disabled={busy || quote.state !== "PRICED"}>
          {quote.approval_needed === "MANAGER" ? "Submit for approval" : "Approve within policy"}
        </button>{" "}
        <button onClick={issue} disabled={busy || quote.state !== "APPROVED"}>Issue</button>
      </p>
      {quote.state === "PENDING_APPROVAL" && <p className="muted">Version {quote.version} is waiting for a sales manager.</p>}
      {error && <p className="error">{error}</p>}
    </section>
  );
}
