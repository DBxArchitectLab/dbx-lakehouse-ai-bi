"""/api/products: look up a glove by catalog number (AI-07): the DBxLab SKU and unit, and its GUDID record.
From the tables now; the live AccessGUDID v3 lookup joins in AI-19."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import User, rep
from ..context import Context
from ..deps import context

router = APIRouter(prefix="/api/products", tags=["products"])


@router.get("/lookup")
def lookup(catalog_number: str = Query(min_length=2, max_length=100), user: User = Depends(rep),
           ctx: Context = Depends(context)):
    found = ctx.catalog.lookup(catalog_number.strip())
    if found is None:
        raise HTTPException(status_code=404, detail=f"no DBxLab SKU or GUDID glove with catalog number "
                                                    f"{catalog_number!r}")
    return found
