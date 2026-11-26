# quote_ai_accessgudid: the Unity Catalog HTTP connection to AccessGUDID (AI-19)

The bundle does not declare UC connections, so the connection is created by SQL in the
`quote_ai_agent_registration` job (`functions --live` runs `src/quote_ai/rules/sql/gudid_live_lookup.sql`), or by
Terraform in dbx-platform-infra-azure if the platform team prefers to own it there. Either way the name is
`quote_ai_accessgudid` (isolation rule; the AI-19 slide says `accessgudid`).

## SQL (what the job runs)

```sql
CREATE CONNECTION IF NOT EXISTS quote_ai_accessgudid TYPE HTTP
OPTIONS (host 'https://accessgudid.nlm.nih.gov', port '443', base_path '/api/v3', bearer_token 'none');

CREATE OR REPLACE FUNCTION <catalog>.quote_ai_ai.gudid_live_lookup(di STRING) RETURNS STRING
COMMENT 'Latest public GUDID record for ONE device identifier ...'
RETURN (SELECT http_request(conn => 'quote_ai_accessgudid', method => 'GET',
                            path => concat('/devices/lookup.json?di=', di)).text);

GRANT USE CONNECTION ON CONNECTION quote_ai_accessgudid TO `<the agent's service principal>`;
GRANT EXECUTE ON FUNCTION <catalog>.quote_ai_ai.gudid_live_lookup TO `<the agent's service principal>`;
```

## Terraform (alternative)

```hcl
resource "databricks_connection" "quote_ai_accessgudid" {
  name            = "quote_ai_accessgudid"
  connection_type = "HTTP"
  comment         = "AccessGUDID API v3 for single live lookups by the QuoteAI agent"
  options = {
    host        = "https://accessgudid.nlm.nih.gov"
    port        = "443"
    base_path   = "/api/v3"
    bearer_token = "none"
  }
}
```

## Rules of use

- Live is for ONE device and its freshest record ("is Glove A current?"); search, analytics and eligibility read the
  lakehouse copy (daily, AI-02 / AI-03).
- AccessGUDID publishes no rate limit: the agent caches each lookup for 24 hours per DI and allows at most 5 live
  calls per conversation (`quote_ai.agents.reliability.Turn`).
- When the API is slow or down, the agent answers from the lakehouse copy and says its release date (AI-20); that
  copy can inform but never issue a quote (freshness policy, `QuoteService.issue`).

## Verify before recording

- `CREATE CONNECTION ... TYPE HTTP` options, and whether a `bearer_token` is required when the API needs no key.
- `http_request()` and its result fields.
- Whether an external MCP server through a UC connection is now the preferred route for AccessGUDID.
- AccessGUDID's terms of use for automated lookups.
