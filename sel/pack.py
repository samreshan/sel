"""Business packs: everything that makes Sel specific to one business.

A pack is a directory::

    pack.toml            identity, languages, scorecard, template paths
    facts.toml           short approved statements, each with an id
    docs/*.md|*.txt      product/service documents claims are checked against
    templates/*.md       follow-up templates (optional)

The engine never imports anything business-specific; it only reads a pack.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
DOC_SUFFIXES = {".md", ".txt"}


@dataclass
class Criterion:
    id: str
    title: str
    description: str
    weight: int = 1


@dataclass
class Fact:
    id: str
    statement: str
    source_doc: str | None = None


@dataclass
class Pack:
    root: Path
    id: str
    name: str
    description: str
    languages: list[str]
    week_starts_on: str
    scorecard: list[Criterion]
    facts: dict[str, Fact]
    docs: dict[str, str]  # path relative to root -> text
    templates: dict[str, str]  # template name -> text
    template_paths: dict[str, str] = field(default_factory=dict)

    def source_text(self, source: str) -> str | None:
        """Text for a SourceRef.source: ``docs/<file>`` or ``fact:<id>``."""
        if source.startswith("fact:"):
            fact = self.facts.get(source.removeprefix("fact:"))
            return fact.statement if fact else None
        return self.docs.get(source)

    def criterion(self, criterion_id: str) -> Criterion | None:
        return next((c for c in self.scorecard if c.id == criterion_id), None)


class PackError(ValueError):
    pass


def load_pack(path: str | Path) -> Pack:
    root = Path(path)
    manifest = root / "pack.toml"
    if not manifest.is_file():
        raise PackError(f"{root}: missing pack.toml")
    with manifest.open("rb") as f:
        data = tomllib.load(f)

    meta = data.get("pack", {})
    facts: dict[str, Fact] = {}
    facts_file = root / "facts.toml"
    if facts_file.is_file():
        with facts_file.open("rb") as f:
            for raw in tomllib.load(f).get("fact", []):
                facts[raw["id"]] = Fact(**raw)

    docs: dict[str, str] = {}
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        for p in sorted(docs_dir.rglob("*")):
            if p.is_file() and p.suffix in DOC_SUFFIXES:
                docs[p.relative_to(root).as_posix()] = p.read_text(encoding="utf-8")

    template_paths = dict(data.get("templates", {}))
    templates = {
        name: (root / rel).read_text(encoding="utf-8")
        for name, rel in template_paths.items()
        if (root / rel).is_file()
    }

    pack = Pack(
        root=root,
        id=meta.get("id", ""),
        name=meta.get("name", ""),
        description=meta.get("description", ""),
        languages=list(meta.get("languages", [])),
        week_starts_on=meta.get("week_starts_on", "monday").lower(),
        scorecard=[Criterion(**c) for c in data.get("scorecard", [])],
        facts=facts,
        docs=docs,
        templates=templates,
        template_paths=template_paths,
    )
    problems = validate_pack(pack)
    if problems:
        raise PackError(f"{root}: " + "; ".join(problems))
    return pack


def validate_pack(pack: Pack) -> list[str]:
    problems: list[str] = []
    if not pack.id:
        problems.append("pack.id is required")
    if not pack.name:
        problems.append("pack.name is required")
    if pack.week_starts_on not in WEEKDAYS:
        problems.append(f"pack.week_starts_on must be a weekday name, got {pack.week_starts_on!r}")
    if not pack.docs and not pack.facts:
        problems.append("a pack needs at least one document or fact to check claims against")
    if not pack.scorecard:
        problems.append("a pack needs at least one [[scorecard]] criterion")
    ids = [c.id for c in pack.scorecard]
    if len(ids) != len(set(ids)):
        problems.append("scorecard criterion ids must be unique")
    for fact in pack.facts.values():
        if fact.source_doc and fact.source_doc not in pack.docs:
            problems.append(f"fact {fact.id!r} cites missing document {fact.source_doc!r}")
    for name, rel in pack.template_paths.items():
        if name not in pack.templates:
            problems.append(f"template {name!r} points to missing file {rel!r}")
    return problems
