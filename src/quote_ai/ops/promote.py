"""Alias promotion (AI-25): promote, don't rebuild. The release manifest (release/release.yml) names a version of each
model and prompt; promote() moves the champion alias, on the Unity Catalog model and on each Prompt Registry prompt,
to exactly those versions in the TARGET catalog. Nothing is copied or rebuilt; only pointers move.

  load_manifest(path)          the manifest, as a dict
  validate(manifest)           what is wrong with it: required keys, whole-number versions, known model and prompt
                               names, the evaluation package version matching evaluation/quote_ai/v1/VERSION
  plan(manifest, names)        the alias moves, one per model and prompt, each a full name inside the target catalog
  promote(manifest, names, registry)   moves only what differs (idempotent: a second run moves nothing) and refuses a
                               name outside the target catalog; returns one row per alias (moved | unchanged)
  check(manifest, names, registry)     the versions missing in the target catalog (CD runs it before deploying)

Rollback is promote() with the previous release's manifest: the same function, the aliases go back.
The decisions are plain Python (tests/quote_ai/test_promote.py, test_release_manifest.py); MLflow is imported lazily,
in one adapter (MlflowRegistry), only in the quote_ai_promote job and in CD.

  python -m quote_ai.ops.promote --catalog dbxarchitectlab_prod --check            # every version exists
  python -m quote_ai.ops.promote --catalog dbxarchitectlab_prod [--dry-run]       # move the aliases
  python -m quote_ai.ops.promote --get models.win_model                           # one value, for CD
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "release" / "release.yml"
PACKAGE_VERSION = ROOT / "evaluation" / "quote_ai" / "v1" / "VERSION"
ALIAS = "champion"
# The manifest names prompts by use; the Prompt Registry by name (quote_ai.llm.prompts.NAME, quote_ai.llm.rag.PROMPT_NAME).
PROMPTS = {"extraction": "extract_requirements", "comparison": "compare_explain"}
# Models: name -> the logical schema it is registered in (quote_ai.ml.register.model_name).
MODELS = {"win_model": "ml"}
REQUIRED = ("quote_ai", "evaluation_package", "models", "prompts")


@dataclass(frozen=True)
class Move:
    kind: str          # model | prompt
    name: str          # catalog.schema.name
    alias: str
    version: int


class Registry(Protocol):
    def get_alias(self, kind: str, name: str, alias: str) -> int | None: ...
    def set_alias(self, kind: str, name: str, alias: str, version: int) -> None: ...
    def exists(self, kind: str, name: str, version: int) -> bool: ...


def load_manifest(path: str | Path = MANIFEST) -> dict:
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def package_version(path: Path = PACKAGE_VERSION) -> str:
    return path.read_text(encoding="utf-8").strip()


def _whole(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def validate(manifest: dict, expected_package: str | None = None) -> list[str]:
    """Every problem with the manifest, one line each; [] means it can be promoted."""
    errors = [f"missing {k}" for k in REQUIRED if k not in manifest]
    for name, v in (manifest.get("models") or {}).items():
        if name not in MODELS:
            errors.append(f"models.{name}: unknown model (known: {sorted(MODELS)})")
        if not _whole(v):
            errors.append(f"models.{name}: version must be a whole number, got {v!r}")
    for use, v in (manifest.get("prompts") or {}).items():
        if use not in PROMPTS:
            errors.append(f"prompts.{use}: unknown prompt (known: {sorted(PROMPTS)})")
        if not _whole(v):
            errors.append(f"prompts.{use}: version must be a whole number, got {v!r}")
    want = expected_package if expected_package is not None else package_version()
    if "evaluation_package" in manifest and str(manifest["evaluation_package"]) != want:
        errors.append(f"evaluation_package {manifest['evaluation_package']} != evaluation/quote_ai/v1/VERSION {want}")
    return errors


def target_names(catalog: str, schemas: str | dict | None = None) -> dict[str, str]:
    """{ml: cat.quote_ai_ml, ai: cat.quote_ai_ai, ...} for the target: the bundle's resolved schema names."""
    from quote_ai.storage import sql_files
    if isinstance(schemas, str):
        schemas = dict(p.split("=", 1) for p in schemas.split(",") if p)
    return sql_files.names_for(catalog, schemas or {})


def plan(manifest: dict, names: dict[str, str], alias: str = ALIAS) -> list[Move]:
    moves = [Move("model", f"{names[MODELS[m]]}.{m}", alias, int(v)) for m, v in (manifest.get("models") or {}).items()]
    moves += [Move("prompt", f"{names['ai']}.{PROMPTS[u]}", alias, int(v))
              for u, v in (manifest.get("prompts") or {}).items()]
    return moves


def _in_target(move: Move, catalog: str) -> None:
    if not move.name.startswith(f"{catalog}."):
        raise PermissionError(f"{move.kind} {move.name} is outside the target catalog {catalog}: not moved")


def promote(manifest: dict, names: dict[str, str], registry: Registry, dry_run: bool = False) -> list[dict]:
    """Move each alias to the manifest's version, in the target catalog only. A manifest that fails validate() moves
    nothing. Idempotent: an alias already there is reported unchanged and not written again."""
    errors = validate(manifest)
    if errors:
        raise ValueError("release manifest: " + "; ".join(errors))
    moves = plan(manifest, names)
    for m in moves:                       # every name checked before anything moves: all or nothing on scope
        _in_target(m, names["catalog"])
    out = []
    for m in moves:
        before = registry.get_alias(m.kind, m.name, m.alias)
        status = "unchanged" if before == m.version else ("would_move" if dry_run else "moved")
        if status == "moved":
            registry.set_alias(m.kind, m.name, m.alias, m.version)
        out.append(asdict(m) | {"from": before, "status": status})
    return out


def check(manifest: dict, names: dict[str, str], registry: Registry) -> list[str]:
    """The manifest's versions that do not exist in the target catalog (a release cannot point at them)."""
    return [f"{m.kind} {m.name} version {m.version}" for m in plan(manifest, names)
            if not registry.exists(m.kind, m.name, m.version)]


def get(manifest: dict, path: str):
    """A dotted value from the manifest (models.win_model) for CD's deploy variable."""
    v = manifest
    for part in path.split("."):
        v = v[part]
    return v


class InMemoryRegistry:
    """Tests and dry runs: {(kind, name): {"versions": {1, 2}, "aliases": {"champion": 1}}}."""

    def __init__(self, entries: dict | None = None) -> None:
        self.entries = entries or {}
        self.writes: list[tuple] = []

    def _e(self, kind, name):
        return self.entries.setdefault((kind, name), {"versions": set(), "aliases": {}})

    def get_alias(self, kind, name, alias):
        return self._e(kind, name)["aliases"].get(alias)

    def set_alias(self, kind, name, alias, version):
        if version not in self._e(kind, name)["versions"]:
            raise LookupError(f"{kind} {name} has no version {version}")
        self._e(kind, name)["aliases"][alias] = version
        self.writes.append((kind, name, alias, version))

    def exists(self, kind, name, version):
        return version in self._e(kind, name)["versions"]


class MlflowRegistry:
    """Unity Catalog models and the Prompt Registry, through MLflow (registry_uri databricks-uc). The only place this
    module touches a workspace. Verify before recording: MlflowClient.get_model_version_by_alias /
    set_registered_model_alias / get_model_version, and mlflow.genai.load_prompt("prompts:/name@alias") /
    set_prompt_alias / load_prompt("prompts:/name/version") in the installed MLflow 3."""

    def __init__(self) -> None:
        import mlflow
        mlflow.set_registry_uri("databricks-uc")
        self.mlflow, self.client = mlflow, mlflow.MlflowClient()

    def get_alias(self, kind, name, alias):
        try:
            if kind == "model":
                return int(self.client.get_model_version_by_alias(name, alias).version)
            return int(self.mlflow.genai.load_prompt(f"prompts:/{name}@{alias}").version)
        except Exception:  # noqa: BLE001  no such alias yet
            return None

    def set_alias(self, kind, name, alias, version):
        if kind == "model":
            self.client.set_registered_model_alias(name, alias, str(version))
        else:
            self.mlflow.genai.set_prompt_alias(name=name, alias=alias, version=version)

    def exists(self, kind, name, version):
        try:
            if kind == "model":
                self.client.get_model_version(name, str(version))
            else:
                self.mlflow.genai.load_prompt(f"prompts:/{name}/{version}")
            return True
        except Exception:  # noqa: BLE001  not found
            return False


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifest", default=str(MANIFEST))
    p.add_argument("--catalog")
    p.add_argument("--schemas", default="", help="logical=physical,... (${resources.schemas.<key>.name})")
    p.add_argument("--check", action="store_true", help="only report versions missing in the target catalog")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--get", help="print one manifest value, e.g. models.win_model")
    a = p.parse_args(argv)
    manifest = load_manifest(a.manifest)
    if a.get:
        print(get(manifest, a.get))
        return 0
    errors = validate(manifest)
    if errors:
        print("\n".join(errors))
        return 1
    if not a.catalog:
        p.error("--catalog is required to check or promote")
    names = target_names(a.catalog, a.schemas)
    registry = MlflowRegistry()
    if a.check:
        missing = check(manifest, names, registry)
        print("\n".join(missing) or f"QuoteAI {manifest['quote_ai']}: every version exists in {a.catalog}")
        return 1 if missing else 0
    rows = promote(manifest, names, registry, dry_run=a.dry_run)
    print(json.dumps({"release": manifest["quote_ai"], "moves": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
