import { useEffect, useState } from "react";
import { api, type Me, type Quote } from "./api";
import NewRequest from "./pages/NewRequest";
import ConfirmRequirements from "./pages/ConfirmRequirements";
import MyQuotes from "./pages/MyQuotes";
import ProductLookup from "./pages/ProductLookup";
import Product from "./pages/Product";
import MyPipeline from "./pages/MyPipeline";
import AskData from "./pages/AskData";
import QuotePage from "./pages/Quote";
import Shortlist from "./pages/Shortlist";
import Compare from "./pages/Compare";
import Pricing from "./pages/Pricing";
import ApprovalInbox from "./pages/ApprovalInbox";
import Ops from "./pages/Ops";

type Page =
  | "new" | "confirm" | "shortlist" | "compare" | "quote" | "pricing" | "approvals" | "mine" | "lookup" | "product"
  | "pipeline" | "ask" | "ops";

// QuoteAI v3 (AI-21): the Pricing screen for reps; the Approval inbox only for DBxLab_Sales_Managers. The menu shows
// pages by role from /api/approvals/me; the API checks the role again on every call. AI-26: the Ops page, managers only.

export default function App() {
  const [page, setPage] = useState<Page>("new");
  const [quote, setQuote] = useState<Quote | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => {
    api.get<Me>("/api/approvals/me").then(setMe).catch(() => setMe(null));
  }, []);
  const open = (q: Quote) => {
    setQuote(q);
    setPage("confirm");
  };
  const tab = (p: Page, label: string) => (
    <button className={page === p ? "tab on" : "tab"} onClick={() => setPage(p)} disabled={["confirm", "shortlist", "compare", "quote", "pricing"].includes(p) && !quote}>
      {label}
    </button>
  );
  return (
    <div className="app">
      <header>
        <h1>
          QuoteAI <span className="v">v3</span>
        </h1>
        <nav>
          {tab("new", "New request")}
          {tab("confirm", "Confirm requirements")}
          {tab("shortlist", "Shortlist")}
          {tab("compare", "Compare")}
          {tab("quote", "Quote")}
          {tab("pricing", "Pricing")}
          {me?.is_manager && tab("approvals", "Approval inbox")}
          {me?.is_manager && tab("ops", "Ops")}
          {tab("mine", "My quotes")}
          {tab("lookup", "Product lookup")}
          {tab("product", "Product")}
          {tab("pipeline", "My pipeline")}
          {tab("ask", "Ask the data")}
        </nav>
      </header>
      {page === "new" && <NewRequest onCreated={open} />}
      {page === "confirm" && quote && <ConfirmRequirements key={quote.quote_id} quote={quote} onChange={setQuote} />}
      {page === "shortlist" && quote && <Shortlist key={quote.quote_id} quote={quote} onChange={setQuote} />}
      {page === "compare" && quote && <Compare key={quote.quote_id} quote={quote} />}
      {page === "quote" && quote && <QuotePage key={quote.quote_id} quote={quote} onChange={setQuote} />}
      {page === "pricing" && quote && <Pricing key={`${quote.quote_id}-${quote.revision}`} quote={quote} onChange={setQuote} />}
      {page === "approvals" && me?.is_manager && <ApprovalInbox />}
      {page === "ops" && me?.is_manager && <Ops quoteId={quote?.quote_id} />}
      {page === "mine" && <MyQuotes onOpen={open} />}
      {page === "lookup" && <ProductLookup />}
      {page === "product" && <Product />}
      {page === "pipeline" && <MyPipeline />}
      {page === "ask" && <AskData />}
    </div>
  );
}
