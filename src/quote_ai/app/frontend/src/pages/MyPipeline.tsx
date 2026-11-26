import { useEffect, useState } from "react";
import { api, ApiError } from "../api";

type Info = { url: string | null; reason: string; rep: string };

// My pipeline (AI-10): the sales overview AI/BI dashboard, embedded. The same dashboard and the same metric views the
// manager sees; the app adds no query of its own. The dashboard runs with the viewer's own credentials; the filter to
// the rep's own accounts becomes a row filter in AI-24.
export function MyPipeline() {
  const [info, setInfo] = useState<Info | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.get<Info>("/api/pipeline").then(setInfo).catch((e: ApiError) => setError(e.detail));
  }, []);
  if (error) return <section><p className="error">{error}</p></section>;
  if (!info) return <section><p className="muted">Loading...</p></section>;
  return (
    <section>
      <h2>My pipeline</h2>
      {info.url ? (
        <iframe title="My pipeline" src={info.url} className="embed" />
      ) : (
        <p className="muted">The dashboard is not available here: {info.reason}</p>
      )}
      <p className="muted">Signed in as {info.rep}. Synthetic demo data.</p>
    </section>
  );
}

export default MyPipeline;
