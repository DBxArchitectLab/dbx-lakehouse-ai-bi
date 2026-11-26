"""The request's dependencies: the context, and (AI-24) who the request runs as.

  context(request)       built on first use (so importing the app needs no workspace), or set by a test
  identity(ctx, user)    the signed-in rep as a quote_ai.rules.access.Identity: their email, groups and forwarded token
  as_rep(ctx, user)      the rep's data read AS THE REP (storage/user_data.py): accounts, price book, Ask the data.
                         Unity Catalog's row filter and column mask apply because the query carries the rep's token
  user_ws(user)          a WorkspaceClient on the rep's forwarded token (price, quote and Genie calls)
  app_ws()               the app's own service principal: audit writes and evaluation reads only (feedback candidates)

The filter by rep the back end applied in code since AI-07 is gone: routers/quotes.py asks as_rep(...).accounts().
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from quote_ai.rules import access

from .auth import User
from .context import Context, build_context


def context(request: Request) -> Context:
    state = request.app.state
    if getattr(state, "ctx", None) is None:
        state.ctx = build_context()
    return state.ctx


def identity(ctx: Context, user: User) -> access.Identity:
    groups = frozenset(ctx.groups(user.id)) if ctx.groups else frozenset()
    return access.Identity(user=user.id, groups=groups, token=user.token)


def as_rep(ctx: Context, user: User):
    """The rep's data as the rep. A missing user token is a refusal, never a fallback to the app's identity."""
    if ctx.as_user is None:
        raise HTTPException(status_code=503, detail="user authorization is not configured in this app")
    try:
        return ctx.as_user(identity(ctx, user))
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e


def user_ws(user: User):
    """Verify before recording: WorkspaceClient(host=, token=, auth_type="pat") with a Databricks Apps user token."""
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.core import Config
    if not user.token:
        raise HTTPException(status_code=401, detail="no user token forwarded")
    return WorkspaceClient(host=Config().host, token=user.token, auth_type="pat")


def app_ws():
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient()
