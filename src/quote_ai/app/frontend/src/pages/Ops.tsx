import { useEffect, useState } from "react";
import { api, ApiError, type OpsView } from "../api";

// The Ops page (AI-26), for DBxLab_Sales_Managers only: the menu shows it by role and the API checks again. It shows
// what the operating dashboard shows, from the same quote_ai_monitoring tables: freshness per source and input against
// its limit, the SLOs per day, and cost per quote by step including the app's own compute. Red when a source is past
// its limit or the last day missed an SLO. Demo data, labelled.
const usd = (v: number | null | undefined) => (v == null ? "-" : `$${v.toFixed(4)}`);
const pct = (v: number | null | undefined) => (v == null ? "-" : `${Math.round(v * 100)}%`);

export default function Ops({ quoteId }: { quoteId?: string }) {
  const [view, setView] = useState<OpsView | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api
      .get<OpsView>(quoteId ? `/api/ops?quote_id=${encodeURIComponent(quoteId)}` : "/api/ops")
      .then(setView)
      .catch((e: ApiError) => setError(e.detail));
  }, [quoteId]);
  if (error) return <p className="error">{error}</p>;
  if (!view) return <p className="muted">Loading...</p>;
  return (
    <section>
      <h2>
        Ops <span className={view.status === "green" ? "badge ok" : "badge bad"}>{view.status}</span>
      </h2>
      <p className="muted">{view.label}</p>
      {view.breaches.length > 0 && <p className="error">Past the limit: {view.breaches.join("; ")}</p>}

      <h3>Freshness</h3>
      <table>
        <thead>
          <tr><th>Kind</th><th>Source or input</th><th>As of</th><th>Age (h)</th><th>Limit (h)</th><th></th></tr>
        </thead>
        <tbody>
          {view.freshness.map((f) => (
            <tr key={`${f.kind}-${f.name}`} className={f.ok ? "" : "bad"}>
              <td>{f.kind}</td><td>{f.name}</td><td>{f.as_of ?? "-"}</td><td>{f.age_hours ?? "-"}</td>
              <td>{f.limit_hours ?? "reported"}</td><td>{f.ok ? "ok" : "stale"}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Service levels per day</h3>
      <table>
        <thead>
          <tr>
            <th>Day</th><th>Quotes</th><th>p95 (s)</th><th>Under 20 s</th><th>Grounded</th><th>Unknown checks</th>
            <th>Violations</th><th>Refused: stale</th><th>Cost / quote</th><th></th>
          </tr>
        </thead>
        <tbody>
          {view.slo.map((d) => (
            <tr key={d.day} className={d.ok ? "" : "bad"}>
              <td>{d.day}</td><td>{d.quotes}</td><td>{d.p95_latency_s}</td><td>{pct(d.under_target_share)}</td>
              <td>{pct(d.grounded_rate)}</td><td>{pct(d.unknown_share)}</td><td>{d.eligibility_violations ?? "-"}</td>
              <td>{d.stale_refusals}</td><td>{usd(d.cost_per_quote_usd)}</td><td>{d.ok ? "ok" : "missed"}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Cost per quote by step</h3>
      <p>
        {view.cost.quotes} quotes, {usd(view.cost.per_quote_usd)} per quote (the app's compute spread over each day's
        quotes)
      </p>
      <table>
        <tbody>
          {Object.entries(view.cost.by_step_usd).map(([step, v]) => (
            <tr key={step}><td>{step}</td><td>{usd(v)}</td></tr>
          ))}
        </tbody>
      </table>
      {view.quote_cost && (
        <p>
          This quote ({quoteId}): {usd(view.quote_cost.per_quote_usd)}{" "}
          <span className="muted">
            {Object.entries(view.quote_cost.by_step_usd).map(([s, v]) => `${s} ${usd(v)}`).join(" · ")}
          </span>
        </p>
      )}
    </section>
  );
}
