"""Read-only view of data/taxonomy.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
TAXONOMY_PATH = REPO_ROOT / "data" / "taxonomy.yaml"


@dataclass(frozen=True)
class Subtopic:
    id: str          # "D6.1"
    tag: str         # "agents.when"
    name: str
    share: int       # % of its domain
    domain_id: str   # "D6"
    weight: float    # effective % of the whole exam
    jd: tuple[str, ...]


@dataclass(frozen=True)
class Domain:
    id: str
    slug: str
    name: str
    weight: int
    subtopics: tuple[Subtopic, ...]


@dataclass(frozen=True)
class Taxonomy:
    version: int
    domains: tuple[Domain, ...]
    competencies: tuple[str, ...]
    jd_items: dict

    @property
    def subtopics(self) -> tuple[Subtopic, ...]:
        return tuple(s for d in self.domains for s in d.subtopics)

    def subtopic(self, tag: str) -> Subtopic:
        return self._by_tag()[tag]

    def domain(self, domain_id: str) -> Domain:
        return {d.id: d for d in self.domains}[domain_id]

    def has_tag(self, tag: str) -> bool:
        return tag in self._by_tag()

    def _by_tag(self) -> dict[str, Subtopic]:
        return {s.tag: s for s in self.subtopics}


def domain_file_prefix(domain_id: str) -> str:
    """D6 -> D06 (used for file names so they sort)."""
    return f"D{int(domain_id[1:]):02d}"


@lru_cache(maxsize=1)
def load_taxonomy(path: Path = TAXONOMY_PATH) -> Taxonomy:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    domains = []
    for d in raw["domains"]:
        subs = tuple(
            Subtopic(
                id=s["id"], tag=s["tag"], name=s["name"], share=s["share"], domain_id=d["id"],
                weight=d["weight"] * s["share"] / 100, jd=tuple(s.get("jd", [])),
            )
            for s in d["subtopics"]
        )
        domains.append(Domain(id=d["id"], slug=d["slug"], name=d["name"], weight=d["weight"], subtopics=subs))
    return Taxonomy(
        version=raw["version"],
        domains=tuple(domains),
        competencies=tuple(x["id"] for x in raw["cross_cutting"]),
        jd_items=raw["jd_coverage_items"],
    )
