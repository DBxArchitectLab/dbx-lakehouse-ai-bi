import { useEffect, useState } from "react";
import {
  api,
  ApiError,
  newKey,
  postStream,
  type ChatDone,
  type ChatMessage,
  type MemoryProposal,
  type Proposal,
  type Quote,
  type Resume,
  type SourceView,
} from "../api";
import SourceBadge from "./SourceBadge";
import Feedback from "./Feedback";

// The chat with the agent, on the quote page (AI-18). Every question carries the quote and its confirmed
// requirements. The answer streams: a "step" line per tool call, "token" pieces of the answer, "source" badges
// (AI-19), proposal cards (the agent proposes; the rep confirms; QuoteService decides), customer facts to save
// (AI-20), and "done" with the trace ID for the feedback buttons of AI-22. Reopening a quote restores the
// conversation (AI-20).
type Turn = { steps: string[]; sources: SourceView[]; text: string; done?: ChatDone };

export default function ChatPanel({ quote, onChange }: { quote: Quote; onChange?: (q: Quote) => void }) {
  const [history, setHistory] = useState<ChatMessage[]>([]);
  const [turn, setTurn] = useState<Turn | null>(null);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [facts, setFacts] = useState<MemoryProposal[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<Resume>(`/api/quotes/${quote.quote_id}/resume`)
      .then((r) => setHistory(r.messages))
      .catch(() => setHistory([]));
  }, [quote.quote_id]);

  const send = async () => {
    const question = text.trim();
    if (!question) return;
    setBusy(true);
    setError(null);
    setText("");
    setProposals([]);
    setFacts([]);
    const t: Turn = { steps: [], sources: [], text: "" };
    setTurn({ ...t });
    try {
      await postStream(`/api/chat/${quote.quote_id}`, { text: question }, (event, data) => {
        if (event === "step") t.steps = [...t.steps, data.label];
        else if (event === "source") t.sources = [...t.sources, data as SourceView];
        else if (event === "token") t.text += data as string;
        else if (event === "proposal") setProposals((p) => [...p, data as Proposal]);
        else if (event === "memory") setFacts((f) => [...f, data as MemoryProposal]);
        else if (event === "done") t.done = data as ChatDone;
        else if (event === "error") setError(data.detail);
        setTurn({ ...t });
      });
      setHistory((h) => [...h, { role: "user", content: question }, { role: "assistant", content: t.text }]);
    } catch (e) {
      setError((e as ApiError).detail ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  const confirm = (p: Proposal) =>
    fetch(`/api/chat/${quote.quote_id}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": p.idempotency_key || newKey() },
      body: JSON.stringify({ target: p.target, revision: quote.revision }),
    })
      .then(async (r) => {
        const data = await r.json();
        if (!r.ok) throw new ApiError(r.status, data.detail);
        onChange?.(data as Quote);
        setProposals([]);
      })
      .catch((e: ApiError) => setError(e.detail));

  const save = (f: MemoryProposal) =>
    api
      .post(`/api/chat/${quote.quote_id}/memory`, { fact_key: f.fact_key, fact_value: f.fact_value })
      .then(() => setFacts((all) => all.filter((x) => x !== f)))
      .catch((e: ApiError) => setError(e.detail));

  const unique = (s: SourceView[]) => s.filter((x, i) => s.findIndex((y) => y.badge === x.badge) === i);

  return (
    <div className="chat">
      <h3>Ask the agent</h3>
      {history.map((m, i) => (
        <div key={i} className={`msg ${m.role}`}>
          {m.role === "user" ? "You: " : ""}
          {m.content}
        </div>
      ))}
      {turn && busy && (
        <>
          {turn.steps.map((s, i) => (
            <div key={i} className="step">{s}</div>
          ))}
          <div className="msg">{turn.text}</div>
        </>
      )}
      {turn && !busy && turn.sources.length > 0 && (
        <p>
          {unique(turn.sources).map((s, i) => (
            <SourceBadge key={i} source={s} />
          ))}
        </p>
      )}
      {turn?.done?.violations?.length ? <p className="error">Withheld: {turn.done.violations.join("; ")}</p> : null}
      {proposals.map((p, i) => (
        <div key={i} className="card">
          Proposed: move to <span className="state">{p.target}</span> ({p.reason}). Nothing has changed yet.
          <br />
          <button onClick={() => confirm(p)}>Confirm</button> <button onClick={() => setProposals([])}>Dismiss</button>
        </div>
      ))}
      {facts.map((f, i) => (
        <div key={i} className="card">
          {f.question} <button onClick={() => save(f)}>Yes, save</button>{" "}
          <button onClick={() => setFacts((all) => all.filter((x) => x !== f))}>No</button>
        </div>
      ))}
      {error && <p className="error">{error}</p>}
      <p>
        <input
          value={text}
          placeholder="What if size M is out of stock?"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !busy && send()}
        />
        <button onClick={send} disabled={busy || !text.trim()}>
          {busy ? "Working..." : "Ask"}
        </button>
      </p>
      {turn?.done && (
        <p className="muted">
          trace {turn.done.trace_id ?? "-"} · {turn.done.usage.prompt_tokens + turn.done.usage.completion_tokens} tokens
        </p>
      )}
      {turn?.done && !busy && (
        <Feedback key={turn.done.trace_id ?? turn.text} traceId={turn.done.trace_id} quoteId={quote.quote_id} surface="chat" />
      )}
    </div>
  );
}
