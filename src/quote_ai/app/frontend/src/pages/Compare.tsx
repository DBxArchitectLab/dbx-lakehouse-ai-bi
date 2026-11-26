import { useEffect, useState } from "react";
import { api, ApiError, type CompareColumn, type CompareTable, type Explanation, type Quote, type SpecCell } from "../api";
import Feedback from "../components/Feedback";

// Compare (AI-16, AI-17): QuoteAI v2's compare view. Every value comes from the evidence tables and the price book,
// each glove's status from the AI-15 rules; unverified values are amber; there is no quality score. "Explain the
// differences" and "Ask about this product" answer from retrieved documents only, and every statement shows its
// citation and its layer of truth underneath. An answer with no citation reads "no evidence on file", never plain
// text. The knowledge index is filtered by the signed-in rep's accounts before retrieval (AI-17).

const LAYER: Record<string, string> = {
  business_truth: "business truth",
  extracted_claim: "extracted claim",
  source_truth: "reference",
  no_evidence: "no evidence",
};

function Cell({ c }: { c: SpecCell }) {
  if (c.status === "missing") return <span className="muted">no evidence on file</span>;
  const doc = c.document_id ? `${c.document_id} v${c.document_version}` : c.source_id;
  return (
    <span className={`badge ${c.status}`} title={`${c.status}, ${c.source_id}`}>
      {c.value}
      {c.unit ? ` ${c.unit}` : ""} · {c.status === "verified" ? doc : c.status}
    </span>
  );
}

function Answer({ a, onCite, quoteId }: { a: Explanation; onCite: (id: string) => void; quoteId: string }) {
  return (
    <div className="answer">
      {a.fallback && <p className="banner">{a.fallback}</p>}
      {a.statements.length === 0 ? (
        <p><strong>no evidence on file</strong></p>
      ) : (
        <ol>
          {a.statements.map((s, i) => (
            <li key={i}>
              {s.text}{" "}
              <span className={`layer ${s.layer}`}>{LAYER[s.layer] ?? s.layer}</span>{" "}
              <button className="link" onClick={() => onCite(s.citation)}>[{s.citation}]</button>
            </li>
          ))}
        </ol>
      )}
      <p className="muted">
        {a.variant === "buy" ? "Knowledge Assistant" : "QuoteAI RAG"} · asked as {a.asked_as}
        {a.cached ? " · cached for this quote version" : ""}
      </p>
      <Feedback key={a.trace_id ?? a.question} traceId={a.trace_id} quoteId={quoteId} surface="compare" />
    </div>
  );
}

export default function Compare({ quote }: { quote: Quote }) {
  const [table, setTable] = useState<CompareTable | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [answers, setAnswers] = useState<Record<string, Explanation>>({});
  const [questions, setQuestions] = useState<Record<string, string>>({});
  const [variant, setVariant] = useState<"build" | "buy">("build");
  const [opened, setOpened] = useState<{ id: string; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    api.get<CompareTable>(`/api/compare/${quote.quote_id}`).then(setTable).catch((e: ApiError) => setError(e.detail));
  }, [quote.quote_id, quote.version]);

  const ask = (key: string, question: string, product_id?: string) => {
    setBusy(key);
    setError(null);
    api
      .post<Explanation>(`/api/compare/${quote.quote_id}/ask`, { question, product_id, variant })
      .then((a) => setAnswers((prev) => ({ ...prev, [key]: a })))
      .catch((e: ApiError) => setError(e.detail))
      .finally(() => setBusy(null));
  };

  // a citation opens the section it points to; a table cell or a rule result points back at the table
  const cite = (key: string) => (id: string) => {
    const chunk = answers[key]?.retrieved.find((c) => c.chunk_id === id);
    setOpened({ id, text: chunk ? chunk.text : "A value in the table above, or the result of a rule." });
  };

  if (!table) {
    return <section>{error ? <p className="error">{error}</p> : <p className="muted">Loading...</p>}</section>;
  }
  const all: CompareColumn[] = [...table.columns, ...table.needs_evidence];
  return (
    <section>
      <h2>Compare · {quote.quote_id} v{table.version}</h2>
      {error && <p className="error">{error}</p>}
      <table>
        <thead>
          <tr><th></th>{table.columns.map((c) => <th key={c.product_id}>{c.name}</th>)}</tr>
        </thead>
        <tbody>
          <tr>
            <td>status</td>
            {table.columns.map((c) => <td key={c.product_id}><strong>{c.offer}</strong></td>)}
          </tr>
          {table.attributes.map((a) => (
            <tr key={a}>
              <td>{a.split("_").join(" ")}</td>
              {table.columns.map((c) => <td key={c.product_id}><Cell c={c.specs[a]} /></td>)}
            </tr>
          ))}
          <tr>
            <td>documentation</td>
            {table.columns.map((c) => (
              <td key={c.product_id}>{c.completeness.verified} of {c.completeness.required} verified</td>
            ))}
          </tr>
          <tr>
            <td>list price</td>
            {table.columns.map((c) => (
              <td key={c.product_id}>
                {c.commercial.list_price !== null ? `$${c.commercial.list_price.toFixed(2)}` : "-"}
                {c.fit.price_vs_target !== null &&
                  ` (${c.fit.price_vs_target >= 0 ? "+" : ""}${c.fit.price_vs_target.toFixed(2)} vs target)`}
              </td>
            ))}
          </tr>
          <tr>
            <td>unresolved</td>
            {table.columns.map((c) => <td key={c.product_id}>{c.unresolved.join(", ") || "-"}</td>)}
          </tr>
        </tbody>
      </table>
      {table.needs_evidence.length > 0 && (
        <p className="banner">
          Needs evidence, not an option yet:{" "}
          {table.needs_evidence.map((n) => `${n.name} (${(n.failed ?? []).join(", ")})`).join("; ")}
        </p>
      )}
      <p className="muted">{table.label}</p>

      <p>
        <label style={{ display: "inline" }}>
          <input type="checkbox" style={{ width: "auto" }} checked={variant === "buy"}
                 onChange={(e) => setVariant(e.target.checked ? "buy" : "build")} /> Knowledge Assistant
        </label>{" "}
        <button disabled={busy !== null} onClick={() => ask("all", "Explain the differences for the customer")}>
          {busy === "all" ? "Explaining..." : "Explain the differences"}
        </button>
      </p>
      {answers.all && <Answer a={answers.all} onCite={cite("all")} quoteId={quote.quote_id} />}

      <h3>Ask about this product</h3>
      {all.map((c) => (
        <div key={c.product_id} className="ask">
          <strong>{c.name}</strong> {c.status === "needs_evidence" && <span className="muted">(needs evidence)</span>}
          <input value={questions[c.product_id] ?? ""} placeholder="What supports the chemo claim?"
                 onChange={(e) => setQuestions({ ...questions, [c.product_id]: e.target.value })} />
          <button disabled={busy !== null || !(questions[c.product_id] ?? "").trim()}
                  onClick={() => ask(c.product_id, questions[c.product_id], c.product_id)}>
            {busy === c.product_id ? "Asking..." : "Ask"}
          </button>
          {answers[c.product_id] && <Answer a={answers[c.product_id]} onCite={cite(c.product_id)} quoteId={quote.quote_id} />}
        </div>
      ))}
      {opened && (
        <div className="source">
          <h4>{opened.id}</h4>
          <pre className="sql">{opened.text}</pre>
          <button onClick={() => setOpened(null)}>Close</button>
        </div>
      )}
    </section>
  );
}
