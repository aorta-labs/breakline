"""Parity Harness: prove the new model behaves like the old one on real fixtures.

Every check here is deterministic. An LLM judge may be added later as a
secondary, labelled signal; it never decides the verdict.

Fixture file: .breakline/fixtures/<name>.jsonl, one object per line:
  {"id": "...", "model": "<old model id>", "messages": [...], "recorded_output": "<old model's output>",
   "expect": {"json_schema": {...}, "label_field": "intent", "numeric_fields": {"amount": 0.01},
              "regex": "...", "format": "text", "strip_fences": true}}
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from .router import Router, strip_fences


class ParityError(ValueError):
    pass


# ---------- minimal JSON-schema subset (type, required, properties, enum, items) ----------
_TYPES = {"object": dict, "array": list, "string": str, "number": (int, float), "integer": int, "boolean": bool}


def schema_errors(value, schema: dict, path: str = "$") -> list[str]:
    errs = []
    t = schema.get("type")
    if t and (not isinstance(value, _TYPES[t]) or (t in ("number", "integer") and isinstance(value, bool))):
        return [f"{path}: expected {t}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} not in enum {schema['enum']}")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f"{path}.{k}: missing")
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                errs += schema_errors(value[k], sub, f"{path}.{k}")
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            errs += schema_errors(v, schema["items"], f"{path}[{i}]")
    return errs


@dataclass
class CaseResult:
    fixture_id: str
    schema_valid_old: bool
    schema_valid_new: bool
    label_old: object = None
    label_new: object = None
    label_agree: bool | None = None
    numeric_drift: float | None = None   # max relative drift across numeric fields
    numeric_missing: bool = False        # a numeric field the old output had is gone or not a number
    regex_ok: bool | None = None
    fences_stripped: bool = False        # fixture opted in to strip_fences and the new output had fences
    notes: list = field(default_factory=list)
    unchecked: list = field(default_factory=list)   # expectations that could not be evaluated


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


@dataclass
class ParityReport:
    old_model: str
    new_model: str
    cases: list
    cost_old: float
    cost_new: float
    latency_old_ms: float
    latency_new_ms: float
    thresholds: dict
    simulated: bool
    priced: bool = True                  # both models have a price in prices.yaml

    def _rate(self, xs):
        xs = [x for x in xs if x is not None]
        return sum(xs) / len(xs) if xs else None

    @property
    def schema_valid(self):
        return self._rate([c.schema_valid_new for c in self.cases])

    @property
    def label_agreement(self):
        return self._rate([c.label_agree for c in self.cases])

    @property
    def max_numeric_drift(self):
        d = [c.numeric_drift for c in self.cases if c.numeric_drift is not None]
        return max(d) if d else None

    @property
    def cost_delta(self):
        return (self.cost_new - self.cost_old) / self.cost_old if self.priced and self.cost_old else None

    @property
    def failures(self) -> list[str]:
        t, out = self.thresholds, []
        if self.schema_valid is not None and self.schema_valid < t["min_schema_valid"]:
            out.append(f"schema-valid {self.schema_valid:.0%} < {t['min_schema_valid']:.0%}")
        if self.label_agreement is not None and self.label_agreement < t["min_label_agreement"]:
            out.append(f"label agreement {self.label_agreement:.0%} < {t['min_label_agreement']:.0%}")
        if self.max_numeric_drift is not None and self.max_numeric_drift > t["max_numeric_drift"]:
            out.append(f"numeric drift {self.max_numeric_drift:.1%} > {t['max_numeric_drift']:.0%}")
        lost = [c.fixture_id for c in self.cases if c.numeric_missing]
        if lost:
            out.append(f"numeric field missing or not a number in {', '.join(lost)}")
        bad_rx = [c.fixture_id for c in self.cases if c.regex_ok is False]
        if bad_rx:
            out.append(f"regex expectation failed in {', '.join(bad_rx)}")
        if self.cost_delta is not None and self.cost_delta > t["max_cost_increase"]:
            out.append(f"cost +{self.cost_delta:.0%} > +{t['max_cost_increase']:.0%}")
        return out

    @property
    def warnings(self) -> list[str]:
        out = []
        if not self.cases:
            out.append("no fixtures for this model, so nothing was proven")
        else:
            if not self.priced:
                out.append("a model has no entry in prices.yaml, so cost was not checked")
            skipped = [c.fixture_id for c in self.cases if c.unchecked]
            if skipped:
                out.append(f"some expectations could not be checked in {', '.join(skipped)}")
            if self.cost_delta is not None and self.cost_delta > 0:
                out.append(f"cost rises {self.cost_delta:+.0%}")
        return out

    @property
    def verdict(self) -> str:
        if self.failures:
            return "fail"
        if self.warnings:
            return "warn"
        return "pass"

    def to_dict(self) -> dict:
        return {
            "old_model": self.old_model, "new_model": self.new_model, "simulated": self.simulated,
            "verdict": self.verdict, "failures": self.failures, "warnings": self.warnings,
            "thresholds": self.thresholds, "priced": self.priced,
            "schema_valid": self.schema_valid, "label_agreement": self.label_agreement,
            "max_numeric_drift": self.max_numeric_drift, "cost_old": self.cost_old, "cost_new": self.cost_new,
            "cost_delta": self.cost_delta, "latency_old_ms": self.latency_old_ms, "latency_new_ms": self.latency_new_ms,
            "cases": [c.__dict__ for c in self.cases],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ParityReport":
        """Rebuild from result.json. The verdict is recomputed from the cases, never trusted from the file."""
        known = {f.name for f in fields(CaseResult)}
        cases = [CaseResult(**{k: v for k, v in c.items() if k in known}) for c in d["cases"]]
        return cls(d["old_model"], d["new_model"], cases, d["cost_old"], d["cost_new"], d["latency_old_ms"],
                   d["latency_new_ms"], d["thresholds"], d["simulated"], d.get("priced", True))


def load_fixtures(repo: Path | str, directory: Path | str | None = None) -> list[dict]:
    d = Path(directory) if directory else Path(repo) / ".breakline" / "fixtures"
    out = []
    for p in sorted(d.glob("*.jsonl")):
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                fx = json.loads(line)
            except json.JSONDecodeError as e:
                raise ParityError(f"{p.name}:{n}: invalid JSON ({e.msg})") from None
            if not isinstance(fx, dict) or not fx.get("id") or not isinstance(fx.get("messages"), list):
                raise ParityError(f"{p.name}:{n}: a fixture needs an 'id' and a 'messages' list")
            out.append(fx)
    return out


def load_prices(repo: Path | str, path: Path | str | None = None) -> dict:
    p = Path(path) if path else Path(repo) / ".breakline" / "prices.yaml"
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}


def _cost(prices: dict, model: str, tin: int, tout: int):
    pr = prices.get(model)
    if not pr:
        return None
    return tin / 1e6 * pr["input_per_1m"] + tout / 1e6 * pr["output_per_1m"]


def _parse(text: str):
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, f"not JSON ({e.msg})"


def _check_case(fx: dict, old_text: str, new_text: str) -> CaseResult:
    exp = fx.get("expect", {})
    text_mode = exp.get("format") == "text"
    raw_new = new_text
    if exp.get("strip_fences"):
        old_text, new_text = strip_fences(old_text), strip_fences(new_text)
    schema = exp.get("json_schema")

    if text_mode:
        cr = CaseResult(fx["id"], True, True)
        old = new = None
    else:
        old, old_err = _parse(old_text)
        new, new_err = _parse(new_text)
        sv_old = old is not None and (not schema or not schema_errors(old, schema))
        sv_new = new is not None and (not schema or not schema_errors(new, schema))
        cr = CaseResult(fx["id"], sv_old, sv_new)
        if new_err:
            hint = " (output wrapped in ``` fences)" if raw_new.strip().startswith("```") else ""
            cr.notes.append(new_err + hint)
        elif schema:
            cr.notes += schema_errors(new, schema)
    cr.fences_stripped = bool(exp.get("strip_fences")) and new_text != raw_new

    lf = exp.get("label_field")
    if lf:
        if isinstance(old, dict) and lf in old:
            cr.label_old = old[lf]
            cr.label_new = new.get(lf) if isinstance(new, dict) else None
            cr.label_agree = cr.label_old == cr.label_new
            if not cr.label_agree:
                cr.notes.append(f"{lf}: {cr.label_old!r} -> {cr.label_new!r}")
        else:
            cr.unchecked.append(f"label_field {lf!r} not in the old output")
    for k, tol in exp.get("numeric_fields", {}).items():
        a = old.get(k) if isinstance(old, dict) else None
        if not _num(a):
            cr.unchecked.append(f"numeric field {k!r} not a number in the old output")
            continue
        if new is None:
            continue  # the new output did not parse; the schema check already fails this fixture
        b = new.get(k) if isinstance(new, dict) else None
        if not _num(b):
            cr.numeric_missing = True
            cr.notes.append(f"{k}: {a} -> {b!r}")
            continue
        d = abs(b - a) / abs(a) if a else abs(b)
        cr.numeric_drift = d if cr.numeric_drift is None else max(cr.numeric_drift, d)
        if d > tol:
            cr.notes.append(f"{k}: {a} -> {b}")
    if exp.get("regex"):
        cr.regex_ok = re.search(exp["regex"], new_text) is not None
        if not cr.regex_ok:
            cr.notes.append(f"output does not match /{exp['regex']}/")
    if text_mode and cr.regex_ok is None:
        cr.unchecked.append("format 'text' with no regex checks nothing")
    return cr


def run(repo: Path | str, old_model: str, new_model: str, router: Router, thresholds: dict,
        fixtures_dir: Path | str | None = None, prices_path: Path | str | None = None) -> ParityReport:
    fixtures = [f for f in load_fixtures(repo, fixtures_dir) if f.get("model", old_model) == old_model]
    prices = load_prices(repo, prices_path)
    priced = old_model in prices and new_model in prices
    cases, c_old, c_new, l_old, l_new = [], 0.0, 0.0, [], []
    for fx in fixtures:
        o = router.call_model(old_model, fx["messages"], fx)
        n = router.call_model(new_model, fx["messages"], fx)
        if priced:
            c_old += _cost(prices, old_model, o.input_tokens, o.output_tokens)
            c_new += _cost(prices, new_model, n.input_tokens, n.output_tokens)
        l_old.append(o.latency_ms)
        l_new.append(n.latency_ms)
        cases.append(_check_case(fx, o.text, n.text))
    avg = lambda xs: sum(xs) / len(xs) if xs else 0.0
    return ParityReport(old_model, new_model, cases, c_old, c_new, avg(l_old), avg(l_new),
                        thresholds, router.mode == "simulated", priced)
