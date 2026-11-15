"""Regenerate the ingestion bundle resources from config/ingest/sources.yml.

  python scripts/gen_ingest_resources.py           write resources/quote_ai_ingest*.yml
  python scripts/gen_ingest_resources.py --check   exit 1 if they don't match the config (what the test checks)
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quote_ai.ingest.bundle_gen import render, stale_files, write  # noqa: E402
from quote_ai.ingest.config import load_config  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check", action="store_true")
    args = p.parse_args()
    cfg = load_config(ROOT / "config" / "ingest" / "sources.yml")
    if args.check:
        stale = stale_files(cfg, ROOT / "resources")
        for n in stale:
            print(f"resources/{n} doesn't match config/ingest/sources.yml: run python scripts/gen_ingest_resources.py")
        return 1 if stale else 0
    for path in write(cfg, ROOT / "resources"):
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
