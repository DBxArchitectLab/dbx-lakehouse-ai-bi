"""The agent app's entry point (AI-20): QuoteAgent behind quote_ai.agents.server, with the vendored quote_ai package
(scripts/quote_ai/build_app.py) when deployed, or src/ in the repo."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if importlib.util.find_spec("quote_ai") is None:
    for p in (_HERE / "_vendor", _HERE.parents[1]):
        if (p / "quote_ai").is_dir():
            sys.path.insert(0, str(p))
            break

from quote_ai.agents.server import create_app  # noqa: E402

app = create_app()
