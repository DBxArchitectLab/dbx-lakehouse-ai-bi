import { useEffect, useState } from "react";
import { api, ApiError, newKey, postWithKey, type InboxItem, type Quote } from "../api";

// The Approval inbox (AI-21), for DBxLab_Sales_Managers only: the menu shows it by role, and the API checks the role
// again (a hidden page is not a control). A manager approves one exact version: the SKUs, boxes, price, discount,
// margin and evidence snapshot shown here, bound by its terms hash. If the quote changed since, the answer is 409
// "quote changed; reload".
export default function ApprovalInbox() {
  const [items, setItems] = useState<InboxItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = () =>
    api
      .get<InboxItem[]>("/api/approvals")
      .then(setItems)
      .catch((e: ApiError) => setError(e.detail));
  useEffect(() => {
    load();
  }, []);

  const approve = (i: InboxItem) => {
    setError(null);
    postWithKey<Quote>(`/api/approvals/${i.quote_id}/v/${i.version}`, {}, newKey())
      .then((q) => setNote(`${q.quote_id} version ${q.version}: ${q.state}`))
      .catch((e: ApiError) => setError(e.detail))
      .finally(load);
  };
  const reject = (i: InboxItem) => {
    const reason = window.prompt("Reason for rejecting");
    if (!reason) return;
    api
      .post<Quote>(`/api/approvals/${i.quote_id}/v/${i.version}/reject`, { reason })
      .then((q) => setNote(`${q.quote_id}: ${q.state}`))
      .catch((e: ApiError) => setError(e.detail))
      .finally(load);
  };

  return (
    <section>
      <h2>Approval inbox</h2>
      {error && <p className="error">{error}</p>}
      {note && <p className="banner">{note}</p>}
      {items.length === 0 && !error && <p className="muted">Nothing waiting for approval.</p>}
      {items.map((i) => (
        <div key={`${i.quote_id}-${i.version}`} className="card">
          <strong>
            {i.quote_id} · {i.account} · version {i.version}
          </strong>{" "}
          <span className="muted">
            rep {i.rep} · requirements r{i.requirements_version} · delivery {i.delivery_days} days · evidence{" "}
            {i.evidence_snapshot ?? "-"}
          </span>
          <table>
            <thead>
              <tr><th>Size</th><th>SKU</th><th>Boxes</th><th>Price</th><th>List</th><th>Margin</th></tr>
            </thead>
            <tbody>
              {i.lines.map((l) => (
                <tr key={l.size}>
                  <td>{l.size}</td>
                  <td>{l.sku_id}</td>
                  <td>{l.boxes}</td>
                  <td>${l.unit_price.toFixed(2)}</td>
                  <td>${l.list_price.toFixed(2)}</td>
                  <td>{l.margin_pct ?? "-"}%</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p>
            Discount {i.discount_pct}% · total ${i.total.toFixed(2)} · terms {i.terms_hash.slice(0, 12)}
          </p>
          <button onClick={() => approve(i)}>Approve version {i.version}</button>{" "}
          <button onClick={() => reject(i)}>Reject</button>
        </div>
      ))}
    </section>
  );
}
