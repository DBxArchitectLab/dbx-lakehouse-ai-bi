-- The live AccessGUDID record of ONE device, through a governed Unity Catalog connection (AI-19). {ai} is
-- catalog.schema of quote_ai_ai from the bundle. No secrets in code: the connection holds the endpoint (AccessGUDID
-- needs no key today); the agent's identity gets EXECUTE on the function and USE CONNECTION on the connection
-- (src/quote_ai/jobs/agent_job.py grants), nothing else.
-- Live is for a single, fresh lookup; search, filters, analytics and eligibility keep using the lakehouse copy. The
-- agent caches a lookup a day per DI (quote_ai.gudid.api_client.LiveGudid) and falls back to the copy, labelled, when
-- the API is slow or down (AI-20).
-- The draft's slide names the connection accessgudid; QuoteAI's isolation rule makes it quote_ai_accessgudid.
-- Verify before recording: CREATE CONNECTION ... TYPE HTTP options (host, port, base_path, bearer_token when no
-- key is needed), http_request(conn, method, path) and its .text field, and AccessGUDID's terms of use.

CREATE CONNECTION IF NOT EXISTS quote_ai_accessgudid TYPE HTTP
OPTIONS (host 'https://accessgudid.nlm.nih.gov', port '443', base_path '/api/v3', bearer_token 'none')
COMMENT 'AccessGUDID API v3 (public FDA device records), for single live lookups by the QuoteAI agent';

CREATE OR REPLACE FUNCTION {ai}.gudid_live_lookup(di STRING)
RETURNS STRING
COMMENT 'Latest public GUDID record for ONE device identifier, from the AccessGUDID API v3. Use only to confirm a single device is current; use the lakehouse tables for search.'
RETURN (SELECT http_request(conn => 'quote_ai_accessgudid', method => 'GET',
                            path => concat('/devices/lookup.json?di=', di)).text);
