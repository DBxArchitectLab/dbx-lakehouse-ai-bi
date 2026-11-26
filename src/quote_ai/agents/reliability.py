"""When a tool misbehaves (AI-18, extended in AI-19). Three rules, in code, not in the prompt.

AI-18  call_tool   every call has a time limit (10 s); transient errors only (a timeout, 429, 503) are retried, up to
                   3 times, after 1, 2 and 4 s plus a little jitter; then ToolUnavailable. A rule's answer (a
                   PricingError, a verified_mismatch) is an answer, returned by the tool and never retried; a refused
                   permission (PERMISSION_DENIED) is never retried either.
AI-19  Turn        the calls of one agent turn: a duplicate (same tool, same arguments) gets the first result back,
                   so one live lookup however often it is asked; a tool that ran out of retries is marked down and
                   named in its result ({"unavailable": ...}), never filled in with a guess, and not called again in
                   the turn; a limit on live calls per conversation (AccessGUDID publishes no rate limit).
"""
from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, field

TIMEOUT_S = 10.0
RETRIES = 3


class RateLimited(RuntimeError):
    """HTTP 429."""


class ServiceUnavailable(RuntimeError):
    """HTTP 503, a server that is down or paused."""


TRANSIENT = (TimeoutError, RateLimited, ServiceUnavailable)


class ToolUnavailable(RuntimeError):
    """A tool that still fails after its retries."""

    def __init__(self, name: str, attempts: list[dict]) -> None:
        super().__init__(f"{name} could not be reached after {len(attempts)} attempts")
        self.name, self.attempts = name, attempts


class ToolRefused(RuntimeError):
    """The tool's identity has no grant for this call (PERMISSION_DENIED): least privilege, never retried."""


def backoff(attempt: int, rand: Callable[[], float] = random.random) -> float:
    """1, 2, 4 s, each plus up to 0.3 s of jitter, so many callers don't retry at the same moment."""
    return 2 ** attempt + rand() * 0.3


def run_with_timeout(fn: Callable[[dict], dict], args: dict, timeout: float) -> dict:
    """Run one call in a worker thread and stop waiting after `timeout` seconds (TimeoutError). The slow call is
    abandoned, not killed: tools must be safe to abandon (they are reads, or a proposal that writes nothing)."""
    ex = ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(fn, args)
    try:
        return fut.result(timeout=timeout)
    except FuturesTimeout:
        raise TimeoutError(f"no answer within {timeout:g} s") from None
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def call_tool(name: str, args: dict, run: Callable[[str, dict], dict], timeout: float = TIMEOUT_S,
              retries: int = RETRIES, sleep: Callable[[float], None] = time.sleep,
              rand: Callable[[], float] = random.random, attempts: list[dict] | None = None) -> dict:
    """One tool call with a timeout and retries on transient errors only. `attempts` (if given) records each try:
    the attempt, the error and the wait before the next one, which the trace shows."""
    log = attempts if attempts is not None else []
    for attempt in range(retries + 1):
        try:
            return run_with_timeout(lambda a: run(name, a), args, timeout)
        except TRANSIENT as e:
            wait = backoff(attempt, rand) if attempt < retries else 0.0
            log.append({"attempt": attempt + 1, "error": f"{type(e).__name__}: {e}", "waited_s": round(wait, 2)})
            if attempt == retries:
                raise ToolUnavailable(name, log) from e
            sleep(wait)
    raise AssertionError("unreachable")


def call_key(name: str, args: Mapping) -> tuple[str, str]:
    return name, json.dumps(args, sort_keys=True, default=str)


@dataclass
class Turn:
    """The tool calls of one agent turn (AI-19). missing: what each tool would have told the rep ("buying history"),
    so an unavailable result names it. limits: the most calls a tool may make in one conversation."""
    missing: Mapping[str, str] = field(default_factory=dict)
    limits: Mapping[str, int] = field(default_factory=dict)
    results: dict = field(default_factory=dict)
    down: dict = field(default_factory=dict)
    log: list[dict] = field(default_factory=list)      # every call asked for: name, args, how it was served
    counts: dict = field(default_factory=dict)

    def call(self, name: str, args: dict, run: Callable[[str, dict], dict], **retry) -> tuple[dict, str]:
        """(output, how): how is run, duplicate, down, limit or refused."""
        key = call_key(name, args)
        if key in self.results:                                   # duplicate call: the first result
            self.log.append({"name": name, "args": args, "served": "duplicate"})
            return self.results[key], "duplicate"
        if name in self.down:                                     # ran out of retries earlier in this turn
            self.log.append({"name": name, "args": args, "served": "down"})
            return self.down[name], "down"
        if name in self.limits and self.counts.get(name, 0) >= self.limits[name]:
            out = {"refused": name, "reason": f"at most {self.limits[name]} {name} calls per conversation"}
            self.log.append({"name": name, "args": args, "served": "limit"})
            return out, "limit"
        self.counts[name] = self.counts.get(name, 0) + 1
        attempts: list[dict] = []
        how = "run"
        try:
            out = call_tool(name, args, run, attempts=attempts, **retry)
        except ToolUnavailable as e:
            what = self.missing.get(name, name)
            out = {"unavailable": name, "missing": what, "attempts": e.attempts,
                   "message": f"{what} is unavailable right now ({e}); do not answer that part from memory"}
            self.down[name] = out
            how = "down"
        except ToolRefused as e:
            out = {"refused": name, "error": "PERMISSION_DENIED", "reason": str(e)}
            how = "refused"
        self.results[key] = out
        self.log.append({"name": name, "args": args, "served": how, "attempts": attempts})
        return out, how
