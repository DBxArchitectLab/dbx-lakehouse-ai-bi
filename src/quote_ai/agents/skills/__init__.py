"""Agent Skills (AI-19): a folder with a SKILL.md (front matter: name and description; then the instructions) and
optional files. The system prompt lists only each skill's name and description; the body loads when the agent calls
load_skill, so a long playbook costs tokens only in the conversations that need it.

A skill explains how to talk about rules; it never holds one. Discount limits are enforced by
quote_ai.rules.pricing, eligibility by quote_ai.rules.eligibility.

Verify before recording: whether the Agent Runtime / Databricks agent framework loads SKILL.md folders natively; if
it does, this loader stays as the local and test path and points at the same folders.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent


class SkillError(LookupError):
    pass


def parse(text: str) -> tuple[dict, str]:
    """(front matter, body) of a SKILL.md."""
    if not text.startswith("---"):
        raise SkillError("a SKILL.md starts with front matter (---)")
    _, front, body = text.split("---", 2)
    meta = yaml.safe_load(front) or {}
    if not meta.get("name") or not meta.get("description"):
        raise SkillError("a skill needs a name and a description")
    return meta, body.strip()


class SkillLibrary:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.skills: dict[str, tuple[dict, str, Path]] = {}
        for f in sorted(root.glob("*/SKILL.md")):
            meta, body = parse(f.read_text(encoding="utf-8"))
            self.skills[meta["name"]] = (meta, body, f.parent)

    def list(self) -> list[dict]:
        """What goes in the system prompt: name and description only."""
        return [{"name": n, "description": m["description"]} for n, (m, _, _) in self.skills.items()]

    def load(self, name: str, file: str | None = None) -> dict:
        if name not in self.skills:
            raise SkillError(f"no skill {name!r}; skills: {', '.join(self.skills)}")
        meta, body, folder = self.skills[name]
        files = sorted(p.name for p in folder.iterdir() if p.is_file() and p.name != "SKILL.md")
        if file is not None:
            if file not in files:
                raise SkillError(f"{name} has no file {file!r}")
            return {"name": name, "file": file, "text": (folder / file).read_text(encoding="utf-8")}
        sections = [l.lstrip("# ").strip() for l in body.splitlines() if l.startswith("## ")]
        return {"name": name, "description": meta["description"], "instructions": body, "files": files,
                "sections": sections}

    def always_in_prompt(self) -> str:
        """The playbook pasted into every prompt: the design we measure against (tokens with and without)."""
        return "\n\n".join(body for _, body, _ in self.skills.values())
