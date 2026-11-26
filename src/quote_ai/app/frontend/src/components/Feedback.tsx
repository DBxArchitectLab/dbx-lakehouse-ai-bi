import { useState } from "react";
import { api, ApiError } from "../api";

// AI-22: thumbs up or down, and an optional comment, under every answer. The back end saves the rating on the MLflow
// trace of this exact answer (rep_rating, source: the signed-in rep); a thumbs down also becomes a candidate
// evaluation case. A person writes its expected result before it joins the evaluation package.
type Props = { traceId: string | null | undefined; quoteId?: string; surface: "chat" | "compare" | "ask_data" };

export default function Feedback({ traceId, quoteId, surface }: Props) {
  const [down, setDown] = useState(false);
  const [comment, setComment] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function send(thumbsUp: boolean) {
    setError(null);
    try {
      await api.post("/api/feedback", {
        trace_id: traceId ?? null,
        thumbs_up: thumbsUp,
        comment: comment.trim() || null,
        quote_id: quoteId ?? null,
        surface,
      });
      setSaved(thumbsUp ? "Thanks." : "Thanks: saved for review.");
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  if (saved) return <p className="muted">{saved}</p>;
  return (
    <div className="feedback">
      <button title="Good answer" onClick={() => send(true)}>👍</button>{" "}
      <button title="Something is wrong" onClick={() => setDown(true)}>👎</button>
      {down && (
        <span>
          {" "}
          <input value={comment} placeholder="What was wrong?" onChange={(e) => setComment(e.target.value)} />
          <button onClick={() => send(false)}>Send</button>
        </span>
      )}
      {error && <span className="error"> {error}</span>}
    </div>
  );
}
