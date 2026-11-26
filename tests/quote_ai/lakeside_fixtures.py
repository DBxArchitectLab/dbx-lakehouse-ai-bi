"""The Lakeside fixtures, re-exported: they live in quote_ai.fixtures.lakeside since AI-22, so the evaluation package
runs without pytest. Tests keep importing `lakeside_fixtures as fx`."""
from quote_ai.fixtures.lakeside import *  # noqa: F401,F403
from quote_ai.fixtures.lakeside import _row  # noqa: F401
