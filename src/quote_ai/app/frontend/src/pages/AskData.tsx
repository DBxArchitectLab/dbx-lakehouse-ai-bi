import { useState } from "react";
import { api, ApiError, type Answer } from "../api";

// Ask the data (AI-11): a question in plain language to the DBxLab sales analyst (a Genie Agent), through the
// Conversation API. Shows the answer, the result table and the SQL Genie wrote. Until AI-24 the app asks as its own
// service principal, so the panel answers only from data every rep may see.
export default function AskData() {
  const [question, setQuestion] = useState("What has Lakeside bought in the last year, and at what price?");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ask = () => {
    setBusy(true);
    setError(null);
    setAnswer(null);
    api.post<Answer>("/api/ask-data", { question })
      .then(setAnswer)
      .catch((e: ApiError) => setError(e.detail))
      .finally(() => setBusy(false));
  };
  return (
    <section>
      <h2>Ask the data</h2>
      <label>
        Question <input value={question} onChange={(e) => setQuestion(e.target.value)} />
      </label>
      <button onClick={ask} disabled={busy || question.trim().length < 3}>{busy ? "Asking..." : "Ask"}</button>
      {error && <p className="error">{error}</p>}
      {answer && (
        <>
          <p>{answer.text}</p>
          {answer.columns.length > 0 && (
            <table>
              <thead>
                <tr>{answer.columns.map((c) => <th key={c}>{c}</th>)}</tr>
              </thead>
              <tbody>
                {answer.rows.map((r, i) => (
                  <tr key={i}>{r.map((v, j) => <td key={j}>{String(v ?? "-")}</td>)}</tr>
                ))}
              </tbody>
            </table>
          )}
          {answer.sql && <pre className="sql">{answer.sql}</pre>}
          <p className="muted">Asked as {answer.asked_as}: you see only your accounts, and cost only if you are a manager. Data answers only: no recommendation.</p>
        </>
      )}
    </section>
  );
}
