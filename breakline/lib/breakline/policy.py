"""Trust & Policy Gateway: per-repo allow / ask / deny rules.

File: .breakline/policy.yaml (or any path given with --policy)

    replacements:            # old model id -> new model id (the team chooses)
      <old-model-id>: <new-model-id>
    rules:                   # first match wins; default is "ask"
      - match: {path: "tests/**"}
        action: allow
      - match: {path: "billing/**"}
        action: deny
      - match: {provider: anthropic}
        action: ask
    parity:
      min_schema_valid: 1.0
      min_label_agreement: 0.95
      max_cost_increase: 0.25
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ACTIONS = ("allow", "ask", "deny")


class PolicyError(ValueError):
    pass


@dataclass
class Policy:
    replacements: dict[str, str] = field(default_factory=dict)
    rules: list[dict] = field(default_factory=list)
    parity: dict = field(default_factory=lambda: {
        "min_schema_valid": 1.0, "min_label_agreement": 0.95, "max_numeric_drift": 0.05,
        "max_cost_increase": 0.25,
    })

    def decide(self, path: str, provider: str, change_id: str) -> str:
        for rule in self.rules:
            m = rule.get("match", {})
            if "path" in m and not fnmatch.fnmatch(path, m["path"]):
                continue
            if "provider" in m and m["provider"] != provider:
                continue
            if "change" in m and m["change"] != change_id:
                continue
            action = rule.get("action", "ask")
            if action not in ACTIONS:
                raise PolicyError(f"bad policy action {action!r}; use allow, ask or deny")
            return action
        return "ask"


def load(repo: Path | str, path: Path | str | None = None) -> Policy:
    p = Path(path) if path else Path(repo) / ".breakline" / "policy.yaml"
    if not p.exists():
        if path:
            raise PolicyError(f"policy file not found: {p}")
        return Policy()
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise PolicyError(f"{p}: invalid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise PolicyError(f"{p}: top level must be a mapping")
    reps = raw.get("replacements") or {}
    if not isinstance(reps, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in reps.items()):
        raise PolicyError(f"{p}: replacements must map model id strings to model id strings")
    pol = Policy(replacements=dict(reps), rules=raw.get("rules") or [])
    for r in pol.rules:  # fail early on a bad action
        if r.get("action", "ask") not in ACTIONS:
            raise PolicyError(f"{p}: bad action {r.get('action')!r}; use allow, ask or deny")
    pol.parity.update(raw.get("parity") or {})
    return pol
