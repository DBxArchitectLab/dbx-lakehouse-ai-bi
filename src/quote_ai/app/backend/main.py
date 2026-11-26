"""QuoteAI v1 (AI-07): the FastAPI back end, which also serves the React screens (frontend/dist).

Started by app.yaml (uvicorn backend.main:app). One error handler per refusal, so every route refuses the same way:
a move the state machine doesn't allow, or a quote that changed since the screen read it, is HTTP 409 with the
reason; a rule that refuses (lines that don't add up) is 422; an extraction that doesn't match the schema is 502.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_APP = Path(__file__).resolve().parents[1]
if importlib.util.find_spec("quote_ai") is None:
    # Deployed, the app folder is the app's root: the quote_ai package is vendored next to it
    # (scripts/quote_ai/build_app.py). In the repo, src/ is three levels up.
    for p in (_APP / "_vendor", _APP.parents[1]):
        if (p / "quote_ai").is_dir():
            sys.path.insert(0, str(p))
            break

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from quote_ai.llm.extract import ExtractionError  # noqa: E402
from quote_ai.rules.quote_states import InvalidTransition  # noqa: E402
from quote_ai.services.quote_service import ApprovalRefused, QuoteError  # noqa: E402
from quote_ai.rules.approval import PolicyError  # noqa: E402
from quote_ai.agents.memory import MemoryRefused  # noqa: E402
from quote_ai.storage.lakebase_repository import ReadOnly  # noqa: E402
from quote_ai.storage.repository import QuoteNotFound, StaleRevision  # noqa: E402

from .context import Context  # noqa: E402
from .routers import (approvals, ask_data, chat, compare, documents, evidence, feedback,  # noqa: E402
                      ops, pipeline, price, products, quotes, shortlist, win_chance)


def _error(status: int):
    async def handler(request: Request, exc: Exception) -> JSONResponse:
        detail = f"quote {exc.args[0]} not found" if isinstance(exc, QuoteNotFound) else str(exc)
        return JSONResponse(status_code=status, content={"detail": detail})
    return handler


def create_app(ctx: Context | None = None, serve_frontend: bool = True) -> FastAPI:
    app = FastAPI(title="QuoteAI", version="1",
                  description="QuoteAI v3: simple rules, evidence, analytics, win chance; Season 4: shortlist by "
                              "search and rules, compare with sources; Season 5: the agent chat, memory, pricing and "
                              "the approval inbox (demo data)")
    app.state.ctx = ctx
    app.include_router(quotes.router)
    app.include_router(products.router)
    for r in (evidence.router, documents.router, pipeline.router, ask_data.router):   # AI-08 to AI-11
        app.include_router(r)
    app.include_router(win_chance.router)                                              # AI-14
    app.include_router(shortlist.router)                                               # AI-15
    app.include_router(compare.router)                                                 # AI-16, AI-17
    app.include_router(chat.router)                                                    # AI-18 to AI-20
    app.include_router(price.router)                                                   # AI-21
    app.include_router(approvals.router)                                               # AI-21
    app.include_router(feedback.router)                                                # AI-22
    app.include_router(ops.router)                                                     # AI-26
    for exc, status in ((InvalidTransition, 409), (StaleRevision, 409), (QuoteError, 422), (QuoteNotFound, 404),
                        (ExtractionError, 502), (ApprovalRefused, 403), (PolicyError, 403), (MemoryRefused, 403),
                        (ReadOnly, 503)):
        app.add_exception_handler(exc, _error(status))

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    dist = _APP / "frontend" / "dist"
    if serve_frontend and dist.is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")   # the React build
    return app


app = create_app()
