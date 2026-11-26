import { useState } from "react";
import { api, ApiError, type Quote as QuoteT, type WinChance } from "../api";
import ChatPanel from "../components/ChatPanel";

// Quote (AI-14): the quote's lines and price, and the win chance at a few candidate prices before the rep chooses one.
// The back end asks the win model endpoint once for all the prices. The chance informs the rep; the price on the
// quote still comes from the price book and the policy. Always labelled: a simulation on demo data.
// AI-18: the chat with the agent lives here, so every question carries this quote.
function defaults(q: QuoteT): string[] {
  const list = q.lines.find((l) => l.list_price !== null)?.list_price;
  if (!list) return ["7.60", "7.90", "8.20"];
  return [1.0, 0.7, 0.4].map((d) => (list - d).toFixed(2));
}

export default function Quote({ quote, onChange }: { quote: QuoteT; onChange?: (q: QuoteT) => void }) {
  const [prices, setPrices] = useState<string[]>(defaults(quote));
  const [result, setResult] = useState<WinChance | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const ask = () => {
    setBusy(true);
    setError(null);
    api
      .post<WinChance>("/api/win-chance", { quote_id: quote.quote_id, prices: prices.map(Number) })
      .then(setResult)
      .catch((e: ApiError) => setError(e.detail))
      .finally(() => setBusy(false));
  };

  return (
    <section>
      <h2>
        Quote {quote.quote_id} · {quote.account} · <span className="state">{quote.state}</span>
      </h2>
      <table>
        <thead>
          <tr><th>Size</th><th>Boxes</th><th>SKU</th><th>Price</th><th>List</th></tr>
        </thead>
        <tbody>
          {quote.lines.map((l) => (
            <tr key={l.line_no}>
              <td>{l.size}</td>
              <td>{l.quantity}</td>
              <td>{l.sku_id ?? "-"}</td>
              <td>{l.unit_price?.toFixed(2) ?? "-"}</td>
              <td>{l.list_price?.toFixed(2) ?? "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {quote.label && <p className="muted">{quote.label}</p>}

      <h3>Win chance at a price</h3>
      <p>
        {prices.map((p, i) => (
          <input
            key={i}
            className="price"
            value={p}
            inputMode="decimal"
            onChange={(e) => setPrices(prices.map((x, j) => (j === i ? e.target.value : x)))}
          />
        ))}
        <button onClick={ask} disabled={busy || prices.some((p) => !(Number(p) > 0))}>
          {busy ? "Asking..." : "Win chance"}
        </button>
      </p>
      {error && <p className="error">{error}</p>}
      {result && (
        <>
          <table>
            <thead>
              <tr><th>Price per box</th><th>Win chance</th><th>Vs list</th><th>Needs approval</th></tr>
            </thead>
            <tbody>
              {result.prices.map((p) => (
                <tr key={p.price}>
                  <td>${p.price.toFixed(2)}</td>
                  <td><strong>{Math.round(p.win_chance * 100)}%</strong></td>
                  <td>{p.price_vs_list_pct}%</td>
                  <td>{p.needs_approval ? "yes, a manager" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!result.consistent && <p className="error">Inconsistent: {result.problems.join("; ")}</p>}
          <p className="banner">Win chance: {result.label}</p>
          <p className="muted">{result.note}</p>
        </>
      )}
      <ChatPanel quote={quote} onChange={onChange} />
    </section>
  );
}
