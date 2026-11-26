import { useState } from "react";
import { api, ApiError, type Quote, type Requirement } from "../api";

const FLAGS = ["powder_free", "latex_free", "chemo_tested", "sterile"] as const;

function flag(r: Requirement | undefined): string {
  if (!r || r.value === null) return "not stated";
  return `${r.value ? "yes" : "no"}${r.hard ? " (hard)" : " (preference)"}`;
}

function words(s: string): string {
  return s.split("_").join(" ");
}

// The requirements as read, the missing details as questions (a quantity per size, the selling unit, the gloves in
// one unit), and one button. Then the v1 quick quote: SHORTLISTED, then PRICED, labelled; v1 has no Issue button.
export default function ConfirmRequirements({ quote, onChange }: { quote: Quote; onChange: (q: Quote) => void }) {
  const x = quote.extracted;
  const sizes = quote.requirements.sizes;
  const [qty, setQty] = useState<Record<string, number>>(Object.fromEntries(sizes.map((s) => [s, 0])));
  const [unit, setUnit] = useState(x?.unit ?? "box");
  const [perUnit, setPerUnit] = useState(100);
  const [error, setError] = useState<string | null>(null);
  const total = Object.values(qty).reduce((a, b) => a + (b || 0), 0);
  const fail = (e: ApiError) => setError(e.detail); // a 409 or 422 shows its reason

  const confirm = () => {
    setError(null);
    api
      .post<Quote>(`/api/quotes/${quote.quote_id}/confirm`, {
        revision: quote.revision,
        quantity_by_size: qty,
        unit,
        gloves_per_unit: perUnit,
      })
      .then(onChange)
      .catch(fail);
  };
  const quickQuote = () => {
    setError(null);
    api
      .post<Quote>(`/api/quotes/${quote.quote_id}/quick-quote`, { revision: quote.revision })
      .then(onChange)
      .catch(fail);
  };

  return (
    <section>
      <h2>
        {quote.quote_id} · {quote.account} <span className="state">{quote.state}</span>
      </h2>
      <p className="muted">
        Requirements r{quote.requirements.version} · quote version {quote.version}
      </p>
      {x && (
        <table>
          <tbody>
            <tr><th>Material</th><td>{x.material ?? "not stated"}</td></tr>
            <tr><th>Use</th><td>{x.use ?? "not stated"}</td></tr>
            {FLAGS.map((f) => (
              <tr key={f}><th>{words(f)}</th><td>{flag(x[f])}</td></tr>
            ))}
            <tr><th>Sizes</th><td>{x.sizes.join(", ") || "not stated"}</td></tr>
            <tr><th>Quantity</th><td>{x.quantity} {x.unit ?? ""}</td></tr>
            <tr>
              <th>Target price</th>
              <td>
                {x.target_price
                  ? `${x.target_price.amount} ${x.target_price.currency} per ${x.target_price.per_unit ?? "?"}`
                  : "not stated"}
              </td>
            </tr>
            <tr><th>Delivery</th><td>{x.delivery_days ? `${x.delivery_days} days` : "not stated"}</td></tr>
          </tbody>
        </table>
      )}

      {quote.state === "DRAFT" && (
        <div className="questions">
          <h3>Missing: {words(quote.requirements.missing.join(", ")) || "nothing"}</h3>
          {sizes.map((s) => (
            <label key={s}>
              {s}{" "}
              <input type="number" min={0} value={qty[s] || ""}
                onChange={(e) => setQty({ ...qty, [s]: Number(e.target.value) })} />
            </label>
          ))}
          <label>
            Unit{" "}
            <select value={unit} onChange={(e) => setUnit(e.target.value)}>
              <option value="box">box</option>
              <option value="case">case</option>
            </select>
          </label>
          <label>
            Gloves per {unit}{" "}
            <input type="number" min={1} value={perUnit} onChange={(e) => setPerUnit(Number(e.target.value))} />
          </label>
          <p className="muted">Total {total} of {quote.requirements.quantity ?? "?"}</p>
          <button onClick={confirm}>Confirm requirements</button>
        </div>
      )}

      {quote.lines.length > 0 && (
        <table className="lines">
          <thead>
            <tr><th>#</th><th>Size</th><th>Qty</th><th>SKU</th><th>List price</th><th>Line total</th></tr>
          </thead>
          <tbody>
            {quote.lines.map((l) => (
              <tr key={l.line_no}>
                <td>{l.line_no}</td>
                <td>{l.size}</td>
                <td>{l.quantity} {l.unit}</td>
                <td>{l.sku_id ?? "-"}</td>
                <td>{l.unit_price ?? "-"}</td>
                <td>{l.unit_price === null ? "-" : (l.unit_price * l.quantity).toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {quote.state === "REQUIREMENTS_CONFIRMED" && <button onClick={quickQuote}>Quick quote</button>}

      {quote.label && (
        <div className="banner">
          <strong>{quote.label}</strong> · {quote.product_id} · total {quote.total?.toFixed(2)}
          <ul>
            {Object.entries(quote.checks).map(([k, v]) => (
              <li key={k}>{words(k)}: {v}</li>
            ))}
          </ul>
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </section>
  );
}
