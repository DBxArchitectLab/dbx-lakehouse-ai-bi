"""Build the QuoteAI app's folder for deployment (AI-07): run before `databricks bundle deploy`.

  1. The React screens: npm ci (npm install until a package-lock.json is committed) && npm run build in src/quote_ai/app/frontend -> frontend/dist
  2. The quote_ai package, vendored into src/quote_ai/app/_vendor/quote_ai (the app deploys only its own folder), and
     from AI-20 also into src/quote_ai/agent_app/_vendor/quote_ai (the agent on the Agent Runtime, its own app)

Both outputs are git-ignored (src/quote_ai/app/.gitignore) and synced by the bundle (databricks.yml sync.include).

  python scripts/quote_ai/build_app.py                    # both
  python scripts/quote_ai/build_app.py --skip-frontend    # no Node here: the back end only (the UI is not served)
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "src" / "quote_ai"
APP = PKG / "app"
VENDOR = APP / "_vendor"
AGENT_APP = PKG / "agent_app"                     # AI-20


def vendor(app: Path = APP) -> Path:
    """Copy src/quote_ai into an app folder, without the apps themselves and without caches."""
    target = app / "_vendor" / "quote_ai"
    shutil.rmtree(app / "_vendor", ignore_errors=True)
    shutil.copytree(PKG, target, ignore=shutil.ignore_patterns("app", "agent_app", "__pycache__", "*.pyc"))
    # AI-23: the models per use (config/quote_ai/models.yml) travel with the package: quote_ai.llm.client reads them
    (app / "_vendor" / "quote_ai_config").mkdir()
    shutil.copy2(ROOT / "config" / "quote_ai" / "models.yml", app / "_vendor" / "quote_ai_config" / "models.yml")
    # AI-26: the SLOs and the freshness policy, for the Ops page (quote_ai.ops.monitoring)
    for name in ("slo.yml", "policy.yml"):
        shutil.copy2(ROOT / "config" / "quote_ai" / name, app / "_vendor" / "quote_ai_config" / name)
    return target


def frontend() -> None:
    npm = shutil.which("npm")
    if npm is None:
        sys.exit("npm not found: install Node 20+, or pass --skip-frontend")
    fe = APP / "frontend"
    subprocess.run([npm, "ci" if (fe / "package-lock.json").exists() else "install"], cwd=fe, check=True)
    subprocess.run([npm, "run", "build"], cwd=APP / "frontend", check=True)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--skip-frontend", action="store_true")
    a = p.parse_args(argv)
    if not a.skip_frontend:
        frontend()
    for app in (APP, AGENT_APP):
        print("vendored", vendor(app).relative_to(ROOT))


if __name__ == "__main__":
    main()
