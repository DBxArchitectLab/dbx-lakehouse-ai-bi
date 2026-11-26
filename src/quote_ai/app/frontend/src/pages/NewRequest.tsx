import { useState } from "react";
import { api, ApiError, type Quote } from "../api";

// Paste a request, or upload it as a PDF (AI-09); either way it goes through the same extraction (quote_ai_extract)
// and opens a DRAFT quote. A PDF is written to the documents volume and parsed with ai_parse_document first.
export default function NewRequest({ onCreated }: { onCreated: (q: Quote) => void }) {
  const [account, setAccount] = useState("Lakeside Regional Clinic");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const done = (p: Promise<Quote>) => {
    setBusy(true);
    setError(null);
    p.then(onCreated)
      .catch((e: ApiError) => setError(e.detail))
      .finally(() => setBusy(false));
  };
  const read = () => done(api.post<Quote>("/api/quotes", { account, text }));
  const upload = () =>
    file && done(api.postFile<Quote>(`/api/documents/request?account=${encodeURIComponent(account)}`, file));
  return (
    <section>
      <h2>New request</h2>
      <label>
        Account <input value={account} onChange={(e) => setAccount(e.target.value)} />
      </label>
      <label>
        Request
        <textarea rows={8} value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste the email" />
      </label>
      <button onClick={read} disabled={busy || !text.trim()}>
        {busy ? "Reading..." : "Read request"}
      </button>
      <label>
        Or a PDF <input type="file" accept="application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      </label>
      <button onClick={upload} disabled={busy || !file}>
        {busy ? "Reading..." : "Upload and read"}
      </button>
      {error && <p className="error">{error}</p>}
    </section>
  );
}
