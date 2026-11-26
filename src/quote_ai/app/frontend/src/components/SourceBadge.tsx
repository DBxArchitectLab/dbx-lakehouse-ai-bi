import type { SourceView } from "../api";

// AI-19: where a fact came from, under the answer. The text comes from the back end's one mapping (BADGES in
// routers/chat.py), from the tool result's source, never from the model's text. A rep can tell at a glance whether
// "in commercial distribution" was checked today or comes from last night's copy.
export default function SourceBadge({ source }: { source: SourceView }) {
  const title = [
    `tool: ${source.tool}`,
    source.served === "duplicate" ? "served from the first call in this turn" : "",
    source.fallback ?? "",
    source.sql ? `SQL: ${source.sql}` : "",
    source.missing ? `missing: ${source.missing}` : "",
  ]
    .filter(Boolean)
    .join("\n");
  return (
    <span className={`srcbadge ${source.source}`} title={title}>
      {source.badge}
    </span>
  );
}
