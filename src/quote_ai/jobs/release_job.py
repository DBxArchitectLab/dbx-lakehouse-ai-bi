"""Job entry point for the release (AI-25): resources/quote_ai/quote_ai_promote.job.yml and quote_ai_smoke_test.job.yml.

  check     every model and prompt version in the release manifest exists in the target catalog
  promote   move the champion aliases to the manifest's versions (quote_ai.ops.promote): only pointers move
  smoke     the post-deploy smoke test through the app's API (quote_ai.ops.smoke_test) --app quote-ai-<target>

Common: --manifest ${workspace.file_path}/release/release.yml, --catalog, --schemas logical=physical,...
Runs as sp-dbx-lakehouse in dev-shared and prod (the target's run_as); prod aliases change only here, started by cd.yml.
"""
import argparse
import inspect
import sys
from pathlib import Path

SRC = Path(inspect.currentframe().f_code.co_filename).resolve().parents[2]
sys.path.insert(0, str(SRC))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["check", "promote", "smoke"])
    p.add_argument("--manifest", default=str(SRC.parent / "release" / "release.yml"))
    p.add_argument("--catalog")
    p.add_argument("--schemas", default="")
    p.add_argument("--app", help="smoke: the app's name, quote-ai-<target>")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    if a.command == "smoke":
        from quote_ai.ops import smoke_test
        return smoke_test.main(["--app", a.app])
    from quote_ai.ops import promote
    args = ["--manifest", a.manifest, "--catalog", a.catalog, "--schemas", a.schemas]
    if a.command == "check":
        args.append("--check")
    if a.dry_run:
        args.append("--dry-run")
    return promote.main(args)


if __name__ == "__main__":
    sys.exit(main())
