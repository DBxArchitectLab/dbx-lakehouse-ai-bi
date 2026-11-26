import { useState } from "react";
import { api, ApiError, type EvidenceRow, type SupplierDocument } from "../api";

// The Product page (AI-08, AI-09): every attribute of a glove with a badge per value: verified (green), unverified
// (amber), conflicting (red), superseded (grey). Hover shows the source, the method and the document version. The
// Documents tab lists each supplier document, its version, and the evidence it verified or that was superseded.
// The back end reads the evidence tables; nothing is recomputed here.

function Badge({ row }: { row: EvidenceRow }) {
  const doc = row.document_id ? `, ${row.document_id} v${row.document_version}` : "";
  const why = row.reason ? `\n${row.reason}` : "";
  const who = row.verified_by ? `\nsigned off by ${row.verified_by}` : "";
  return (
    <span className={`badge ${row.verification_status}`}
          title={`${row.source_type} (${row.extraction_method})${doc}${who}${why}`}>
      {row.attribute_value}
      {row.unit ? ` ${row.unit}` : ""} - {row.verification_status}
    </span>
  );
}

function Evidence({ rows }: { rows: EvidenceRow[] }) {
  const attributes = Array.from(new Set(rows.map((r) => r.attribute_name))).sort();
  return (
    <table>
      <thead>
        <tr><th>attribute</th><th>values, by source</th></tr>
      </thead>
      <tbody>
        {attributes.map((a) => (
          <tr key={a}>
            <td>{a.split("_").join(" ")}</td>
            <td>
              {rows.filter((r) => r.attribute_name === a).map((r, i) => (
                <span key={i}>
                  <Badge row={r} />{" "}
                </span>
              ))}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Documents({ docs }: { docs: SupplierDocument[] }) {
  if (!docs.length) return <p className="muted">No supplier document on file: every claim stays unverified.</p>;
  return (
    <table>
      <thead>
        <tr><th>document</th><th>type</th><th>version</th><th>current</th><th>signed off</th><th>verified</th>
          <th>superseded</th></tr>
      </thead>
      <tbody>
        {docs.map((d) => (
          <tr key={`${d.document_id}-${d.version}`}>
            <td>{d.document_id}</td>
            <td>{d.document_type.split("_").join(" ")}</td>
            <td>v{d.version} ({d.effective_date})</td>
            <td>{d.is_current ? "yes" : "no"}</td>
            <td>{d.verified_by ?? "-"}</td>
            <td>{d.verified.join(", ") || "-"}</td>
            <td>{d.superseded.join(", ") || "-"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Product() {
  const [key, setKey] = useState("GLOVE-A-M");
  const [tab, setTab] = useState<"attributes" | "documents">("attributes");
  const [rows, setRows] = useState<EvidenceRow[] | null>(null);
  const [docs, setDocs] = useState<SupplierDocument[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const product = key.split("-").slice(0, 2).join("-");     // GLOVE-A-M -> GLOVE-A
  const open = () => {
    setError(null);
    setRows(null);
    setDocs(null);
    api.get<{ rows: EvidenceRow[] }>(`/api/evidence/${encodeURIComponent(key)}`)
      .then((r) => setRows(r.rows))
      .catch((e: ApiError) => setError(e.detail));
    api.get<{ documents: SupplierDocument[] }>(`/api/documents/${encodeURIComponent(product)}`)
      .then((r) => setDocs(r.documents))
      .catch((e: ApiError) => setError(e.detail));
  };
  return (
    <section>
      <h2>Product</h2>
      <label>
        SKU or product <input value={key} onChange={(e) => setKey(e.target.value.trim())} />
      </label>
      <button onClick={open} disabled={!key}>Open</button>
      {error && <p className="error">{error}</p>}
      {(rows || docs) && (
        <nav>
          <button className={tab === "attributes" ? "tab on" : "tab"} onClick={() => setTab("attributes")}>
            Attributes
          </button>
          <button className={tab === "documents" ? "tab on" : "tab"} onClick={() => setTab("documents")}>
            Documents
          </button>
        </nav>
      )}
      {tab === "attributes" && rows && <Evidence rows={rows} />}
      {tab === "documents" && docs && <Documents docs={docs} />}
      <p className="muted">
        Green: verified (business truth). Amber: an extracted claim, unverified. Red: sources disagree. Grey: an older
        document version. Demo data.
      </p>
    </section>
  );
}
