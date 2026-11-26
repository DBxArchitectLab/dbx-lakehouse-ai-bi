"""/api/evidence (AI-08): a glove's evidence rows, as the evidence tables hold them. Nothing is recomputed here.

  GET /api/evidence/{key}   key: a SKU (GLOVE-A-M) or a product (GLOVE-A): every attribute with its value, unit,
                            source, method, document and verification status (verified, unverified, conflicting,
                            superseded): the badges of the Product page
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path

from ..auth import User, rep
from ..context import Context
from ..deps import context

router = APIRouter(prefix="/api/evidence", tags=["evidence"])


def store(ctx: Context):
    if ctx.evidence is None:
        raise HTTPException(status_code=503, detail="evidence is not configured in this app")
    return ctx.evidence


@router.get("/{key}")
def evidence(key: str = Path(pattern=r"^[A-Za-z0-9_-]{1,64}$"), user: User = Depends(rep),
             ctx: Context = Depends(context)):
    rows = store(ctx).for_product(key)
    if not rows:
        raise HTTPException(status_code=404, detail=f"no evidence for {key!r}")
    return {"key": key, "rows": rows}
