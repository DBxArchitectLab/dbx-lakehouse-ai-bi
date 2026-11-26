"""/api/documents (AI-09): a request as a PDF, and a product's supplier documents.

  POST /api/documents/request?account=...   the body is the PDF (Content-Type: application/pdf). It is written to the
                                            quote_ai_documents volume, parsed with ai_parse_document, and the text
                                            goes through the same extraction as a pasted email: a DRAFT quote
  GET  /api/documents/{product_id}          each document, its version, whether it is current, who signed it off,
                                            and the evidence it verified or that a newer version superseded
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from ..auth import User, rep
from ..context import Context
from ..deps import context
from .evidence import store
from .quotes import view

router = APIRouter(prefix="/api/documents", tags=["documents"])
MAX_BYTES = 10 * 1024 * 1024


@router.post("/request", status_code=201)
async def upload_request(request: Request, account: str = Query(min_length=1, max_length=200),
                         user: User = Depends(rep), ctx: Context = Depends(context)):
    if ctx.documents is None:
        raise HTTPException(status_code=503, detail="the documents volume is not configured in this app")
    data = await request.body()
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=415, detail="send the request as a PDF (application/pdf)")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="the PDF is larger than 10 MB")
    path = ctx.documents.upload_request(data, request.headers.get("x-filename", "request.pdf"))
    text = ctx.documents.read_text(path)
    if not text.strip():
        raise HTTPException(status_code=422, detail="no text could be read from the PDF")
    q = ctx.service.read_request(account, user.id, text, ctx.extractor)
    return view(ctx, q) | {"document_path": path}


@router.get("/{product_id}")
def product_documents(product_id: str = Path(pattern=r"^[A-Za-z0-9_-]{1,64}$"), user: User = Depends(rep),
                      ctx: Context = Depends(context)):
    return {"product_id": product_id, "documents": store(ctx).documents(product_id)}
