"""The signed-in user, from the headers Databricks Apps forwards (X-Forwarded-Email, X-Forwarded-Preferred-Username).

Who may open the app at all is the app's CAN_USE permission (DBxLab_Sales_Reps only, resources/quote_ai/
quote_ai_app.app.yml). The user is recorded as the actor of every move. AI-24: Databricks Apps also forwards the
signed-in user's token (X-Forwarded-Access-Token, limited to the scopes the app declares: sql, dashboards.genie); the
back end queries the rep's data with it (deps.user_ws, storage/user_data.py). The React front end never sees it.
QUOTE_AI_LOCAL_USER signs in a local run.
"""
from __future__ import annotations

import os

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field


class User(BaseModel):
    id: str
    token: str | None = Field(default=None, repr=False, exclude=True)   # AI-24: the forwarded user token


def rep(request: Request) -> User:
    who = (request.headers.get("x-forwarded-email") or request.headers.get("x-forwarded-preferred-username")
           or os.environ.get("QUOTE_AI_LOCAL_USER"))
    if not who:
        raise HTTPException(status_code=401, detail="not signed in")
    return User(id=who, token=request.headers.get("x-forwarded-access-token"))


def workspace_groups(ttl_s: int = 300):
    """AI-21: user email -> the workspace groups they belong to (SCIM, as the app's service principal), cached for a
    few minutes. The approver check uses it: membership of DBxLab_Sales_Managers, never a field in the request.
    Verify before recording: users.list(filter='userName eq "..."', attributes="groups") and group display names;
    whether Databricks Apps can forward group membership directly instead."""
    import time
    cache: dict[str, tuple[float, set[str]]] = {}

    def groups(user: str) -> set[str]:
        hit = cache.get(user)
        if hit and time.monotonic() - hit[0] < ttl_s:
            return hit[1]
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        found = list(w.users.list(filter=f'userName eq "{user}"', attributes="groups"))
        names = {g.display for u in found for g in (u.groups or []) if g.display}
        cache[user] = (time.monotonic(), names)
        return names
    return groups
