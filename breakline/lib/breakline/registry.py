"""Change Registry: typed, validated records of upcoming breaking changes.

Deterministic. The LLM may *propose* records (see extract_candidates), but nothing
enters the registry unless validate_record() accepts it.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# The skill keeps one copy of the registry, in references/.
DATA = Path(__file__).resolve().parents[2] / "references" / "registry.json"
KINDS = {"model_retirement", "auth_change", "endpoint_removal", "version_retirement", "behaviour_change"}
STATUSES = {"verified", "unverified"}


class RegistryError(ValueError):
    pass


@dataclass(frozen=True)
class Change:
    id: str
    provider: str
    kind: str
    symbol: str
    effective_date: dt.date
    evidence_url: str
    evidence_quote: str
    status: str
    default_replacement: str | None = None
    detect_patterns: tuple[str, ...] = field(default_factory=tuple)

    def days_left(self, today: dt.date | None = None) -> int:
        return (self.effective_date - (today or dt.date.today())).days


def validate_record(rec: dict) -> Change:
    """Reject anything without evidence, a parseable date and a known kind."""
    missing = [k for k in ("id", "provider", "kind", "symbol", "effective_date", "evidence_url", "evidence_quote", "status") if not rec.get(k)]
    if missing:
        raise RegistryError(f"{rec.get('id', '?')}: missing {missing}")
    if rec["kind"] not in KINDS:
        raise RegistryError(f"{rec['id']}: unknown kind {rec['kind']!r}")
    if rec["status"] not in STATUSES:
        raise RegistryError(f"{rec['id']}: unknown status {rec['status']!r}")
    if not re.fullmatch(r"https://\S+", rec["evidence_url"]):
        raise RegistryError(f"{rec['id']}: evidence_url must be https")
    try:
        date = dt.date.fromisoformat(rec["effective_date"])
    except ValueError as e:
        raise RegistryError(f"{rec['id']}: bad effective_date") from e
    if rec["kind"] == "model_retirement" and rec["symbol"] not in rec["evidence_quote"]:
        raise RegistryError(f"{rec['id']}: evidence_quote must contain the symbol verbatim")
    for p in rec.get("detect_patterns", []):
        try:
            re.compile(p)
        except re.error as e:
            raise RegistryError(f"{rec['id']}: bad detect_pattern {p!r}") from e
    return Change(
        id=rec["id"], provider=rec["provider"], kind=rec["kind"], symbol=rec["symbol"],
        effective_date=date, evidence_url=rec["evidence_url"], evidence_quote=rec["evidence_quote"],
        status=rec["status"], default_replacement=rec.get("default_replacement"),
        detect_patterns=tuple(rec.get("detect_patterns", [])),
    )


def load(path: Path | str | None = None) -> list[Change]:
    try:
        raw = json.loads(Path(path or DATA).read_text(encoding="utf-8"))
        changes = [validate_record(r) for r in raw["changes"]]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
        raise RegistryError(f"cannot read registry {path or DATA}: {e}") from e
    ids = [c.id for c in changes]
    if len(ids) != len(set(ids)):
        raise RegistryError("duplicate change ids")
    return changes


def extract_candidates(changelog_text: str, router) -> list[dict]:
    """LLM proposes records from a provider changelog; the validator decides."""
    prompt = (
        "Extract breaking changes as a JSON list of objects with keys id, provider, kind, symbol, "
        "effective_date (YYYY-MM-DD), evidence_quote (verbatim from the text), status='unverified'.\n\n"
        + changelog_text
    )
    out = router.complete("extract_change", [{"role": "user", "content": prompt}])
    accepted = []
    for rec in json.loads(out.text):
        if rec.get("evidence_quote", "") not in changelog_text:
            continue  # quote must be verbatim from the source
        try:
            validate_record(rec)
            accepted.append(rec)
        except RegistryError:
            continue
    return accepted
