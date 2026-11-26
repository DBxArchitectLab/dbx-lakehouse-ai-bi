import { useState } from "react";
import { api, ApiError } from "../api";

type Row = Record<string, unknown>;
type Found = { catalog_number: string; skus: Row[]; gudid: Row[] };

const SKU_FIELDS = ["sku_id", "catalog_number", "size", "unit", "gloves_per_unit", "verification_status"];
const GUDID_FIELDS = ["primary_di", "brand_name", "company_name", "catalog_number", "device_description",
  "labeled_no_nrl", "device_sterile"];

function Rows({ rows, fields }: { rows: Row[]; fields: string[] }) {
  return (
    <table>
      <thead>
        <tr>{fields.map((f) => <th key={f}>{f.split("_").join(" ")}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>{fields.map((f) => <td key={f}>{String(r[f] ?? "-")}</td>)}</tr>
        ))}
      </tbody>
    </table>
  );
}

// Look up a glove by catalog number: the DBxLab SKU and its unit, and the GUDID record (from the tables in v1).
export default function ProductLookup() {
  const [number, setNumber] = useState("DBX-NE-M-100");
  const [found, setFound] = useState<Found | null>(null);
  const [error, setError] = useState<string | null>(null);
  const look = () => {
    setError(null);
    setFound(null);
    api
      .get<Found>(`/api/products/lookup?catalog_number=${encodeURIComponent(number)}`)
      .then(setFound)
      .catch((e: ApiError) => setError(e.detail));
  };
  return (
    <section>
      <h2>Product lookup</h2>
      <label>
        Catalog number <input value={number} onChange={(e) => setNumber(e.target.value)} />
      </label>
      <button onClick={look} disabled={!number.trim()}>Look up</button>
      {error && <p className="error">{error}</p>}
      {found && (
        <>
          <h3>DBxLab SKU</h3>
          <Rows rows={found.skus} fields={SKU_FIELDS} />
          <h3>GUDID record</h3>
          {found.gudid.length ? <Rows rows={found.gudid} fields={GUDID_FIELDS} /> : <p>none</p>}
        </>
      )}
    </section>
  );
}
