import { useEffect, useState } from "react";
import { api, ApiError, type Quote } from "../api";

// My quotes: each quote, its state, and its events (who moved it, when, and the trace of the request).
// AI-20: read from Lakebase; "open only" leaves out the finished ones, and opening a quote restores its state,
// shortlist and, new, its conversation with the agent (the chat panel on the Quote page reloads it).
export default function MyQuotes({ onOpen }: { onOpen: (q: Quote) => void }) {
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [openOnly, setOpenOnly] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.get<Quote[]>(`/api/quotes?open=${openOnly}`).then(setQuotes).catch((e: ApiError) => setError(e.detail));
  }, [openOnly]);
  return (
    <section>
      <h2>My quotes</h2>
      <label>
        <input type="checkbox" style={{ width: "auto" }} checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} />{" "}
        open quotes only
      </label>
      {error && <p className="error">{error}</p>}
      {quotes.map((q) => (
        <details key={q.quote_id}>
          <summary>
            <button className="link" onClick={() => onOpen(q)}>{q.quote_id}</button> · {q.account} ·{" "}
            <span className="state">{q.state}</span> · v{q.version} · {q.events?.length ?? 0} events
          </summary>
          <table>
            <thead>
              <tr><th>Action</th><th>From</th><th>To</th><th>Actor</th><th>At</th><th>Trace</th></tr>
            </thead>
            <tbody>
              {(q.events ?? []).map((e, i) => (
                <tr key={i}>
                  <td>{e.action}</td>
                  <td>{e.from_state ?? "-"}</td>
                  <td>{e.to_state}</td>
                  <td>{e.actor}</td>
                  <td>{new Date(e.at).toLocaleString()}</td>
                  <td>{e.trace_id ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      ))}
    </section>
  );
}
