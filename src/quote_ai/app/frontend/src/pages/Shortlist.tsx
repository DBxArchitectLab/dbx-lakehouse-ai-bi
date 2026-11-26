import { useState } from "react";
import { api, ApiError, type Candidate, type Quote, type RequirementCheck, type ShortlistResult } from "../api";

// Shortlist (AI-15): Find products on a confirmed quote. The back end searches (AI Search), runs the eligibility
// engine on every candidate and fulfilment on the eligible ones, and QuoteService moves the quote to SHORTLISTED.
// This screen only shows the rules' answer: green verified match, red verified mismatch, grey unknown ("evidence
// missing"). Grey is never drawn as a pass. A glove that misses the hard date is "alternative, needs acceptance".

const LABEL: Record<RequirementCheck["result"], string> = {
  verified_match: "match",
  verified_mismatch: "mismatch",
  unknown: "evidence missing",
};

function Chip({ c }: { c: RequirementCheck }) {
  const why = [c.reason, ...c.evidence.map((e) => `used: ${e}`), ...c.claims.map((x) => `not counted: ${x}`)];
  return (
    <span className={`chip ${c.result}`} title={why.join("\n")}>
      {c.requirement.split("_").join(" ")}: {LABEL[c.result]}
    </span>
  );
}

function Group({ title, items, open, onOpen }: {
  title: string; items: Candidate[]; open: string | null; onOpen: (id: string | null) => void;
}) {
  if (!items.length) return null;
  return (
    <>
      <h3>{title} ({items.length})</h3>
      <table>
        <thead>
          <tr><th>Glove</th><th>Checks</th>{title === "Eligible" && <th>Fulfilment</th>}</tr>
        </thead>
        <tbody>
          {items.map((c) => (
            <tr key={c.product_id}>
              <td>
                <button className="link" onClick={() => onOpen(open === c.product_id ? null : c.product_id)}>
                  {c.name || c.product_id}
                </button>
              </td>
              <td>
                {c.checks.map((k) => <Chip key={k.requirement} c={k} />)}
                {open === c.product_id && (
                  <ul className="muted">
                    {c.checks.map((k) => (
                      <li key={k.requirement}>
                        {k.requirement}: {k.reason}
                        {k.claims.length > 0 && <> · on file, not counted: {k.claims.join("; ")}</>}
                      </li>
                    ))}
                  </ul>
                )}
              </td>
              {title === "Eligible" && c.fulfilment && (
                <td className={c.fulfilment.on_time ? "" : "late"}>
                  {c.fulfilment.status}
                  {c.fulfilment.later > 0 && ` (${c.fulfilment.now} now, ${c.fulfilment.later} by day ${c.fulfilment.later_day ?? "?"})`}
                  <br />
                  <strong>{c.fulfilment.label}</strong>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export default function Shortlist({ quote, onChange }: { quote: Quote; onChange: (q: Quote) => void }) {
  const [result, setResult] = useState<ShortlistResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  const find = () => {
    setBusy(true);
    setError(null);
    api
      .post<ShortlistResult>(`/api/shortlist/${quote.quote_id}`, { revision: quote.revision })
      .then((r) => {
        setResult(r);
        onChange(r.quote);
      })
      .catch((e: ApiError) => setError(e.detail))
      .finally(() => setBusy(false));
  };

  const s = result?.shortlist;
  return (
    <section>
      <h2>
        Shortlist {quote.quote_id} · {quote.account} · <span className="state">{quote.state}</span>
      </h2>
      <button onClick={find} disabled={busy || !["REQUIREMENTS_CONFIRMED", "SHORTLISTED"].includes(quote.state)}>
        {busy ? "Searching..." : "Find products"}
      </button>
      {error && <p className="error">{error}</p>}
      {result && !result.moved && <p className="banner">Not shortlisted: {result.reason}</p>}
      {s && (
        <>
          <p className="muted">
            {s.candidates.length} candidates for "{s.query}" · evidence {s.evidence_snapshot} · stock{" "}
            {s.inventory_snapshot || "-"}
          </p>
          <Group title="Eligible" items={s.eligible} open={open} onOpen={setOpen} />
          <Group title="Needs evidence" items={s.needs_evidence} open={open} onOpen={setOpen} />
          <Group title="Excluded" items={s.excluded} open={open} onOpen={setOpen} />
        </>
      )}
    </section>
  );
}
