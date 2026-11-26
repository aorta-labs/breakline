"""Library tests: the original proof-of-concept tests, plus tests for each fix made while vendoring."""
import datetime as dt
import json

import pytest

from breakline import codemod, parity, policy, registry, report, scanner
from breakline.router import Router, strip_fences


def _rec(**kw):
    base = {"id": "x", "provider": "openai", "kind": "model_retirement", "symbol": "m1", "effective_date": "2027-01-01",
            "evidence_url": "https://a", "evidence_quote": "m1 gone", "status": "unverified"}
    return {**base, **kw}


def test_registry_validates_and_rejects():
    changes = registry.load()
    assert any(c.symbol == "gpt-4o-2024-08-06" for c in changes)
    with pytest.raises(registry.RegistryError):
        registry.validate_record(_rec(effective_date="2027-13-01"))
    with pytest.raises(registry.RegistryError):  # quote must contain the symbol
        registry.validate_record(_rec(evidence_quote="retiring soon"))
    with pytest.raises(registry.RegistryError):
        registry.validate_record(_rec(kind="auth_change", detect_patterns=["("]))


def test_scanner_finds_all_contexts(repo):
    f = scanner.scan(repo, registry.load())
    ctx = {(x.path, x.context) for x in f}
    assert ("app/triage.py", "model_kwarg") in ctx
    assert ("app/triage.py", "env_default") in ctx
    assert ("app/summarize.py", "assignment") in ctx
    assert ("config/.env.example", "config") in ctx
    assert ("web/shopify.ts", "model_kwarg") in ctx
    assert any(x.symbol == "non_expiring_offline_token" for x in f)


def test_scanner_finds_unquoted_yaml_and_non_ascii_lines(tmp_path):
    (tmp_path / "cfg.yaml").write_text("llm:\n  model: gpt-4o-2024-08-06   # old\n- name: gpt-4o-2024-08-06\n", encoding="utf-8")
    (tmp_path / "a.py").write_text('x = "é€" ; model = "gpt-4o-2024-08-06"\n', encoding="utf-8")
    f = scanner.scan(tmp_path, registry.load())
    assert {(x.path, x.line) for x in f} == {("cfg.yaml", 2), ("cfg.yaml", 3), ("a.py", 1)}
    py = next(x for x in f if x.path == "a.py")
    assert 'x = "é€" ; model = "gpt-4o-2024-08-06"'[py.col:py.end_col] == "gpt-4o-2024-08-06"


def test_policy_deny_and_codemod_parses(repo):
    changes = registry.load()
    pol = policy.load(repo)
    ps = codemod.plan(repo, scanner.scan(repo, changes), changes, pol)
    denied = [s for s in ps.sites if s.status == "skipped_deny"]
    assert denied and denied[0].finding.path == "app/billing/invoice_parser.py"
    codemod.apply(repo, ps)
    assert "gpt-4o-2024-08-06" not in (repo / "app/triage.py").read_text()
    assert "gpt-4.1-2025-04-14" in (repo / "app/billing/invoice_parser.py").read_text()
    left = scanner.scan(repo, changes)
    assert {x.path for x in left if x.symbol.startswith("gpt")} == {"app/billing/invoice_parser.py"}


def test_codemod_keeps_crlf_and_rejects_unsafe_replacement(tmp_path):
    (tmp_path / "a.py").write_bytes(b'model = "gpt-4o-2024-08-06"\r\nprint(1)\r\n')
    changes = registry.load()
    ps = codemod.plan(tmp_path, scanner.scan(tmp_path, changes), changes, policy.Policy(replacements={"gpt-4o-2024-08-06": "new-model"}))
    codemod.apply(tmp_path, ps)
    assert (tmp_path / "a.py").read_bytes() == b'model = "new-model"\r\nprint(1)\r\n'
    (tmp_path / "b.py").write_text('m = "gpt-4o-2024-08-06"\n')
    evil = codemod.plan(tmp_path, [f for f in scanner.scan(tmp_path, changes) if f.path == "b.py"], changes,
                        policy.Policy(replacements={"gpt-4o-2024-08-06": 'x"; import os #'}))
    assert evil.sites[0].status == "needs_human" and not evil.new_text


def test_policy_errors_are_clear(tmp_path):
    (tmp_path / ".breakline").mkdir()
    (tmp_path / ".breakline" / "policy.yaml").write_text("rules:\n  - match: {path: '*'}\n    action: yolo\n")
    with pytest.raises(policy.PolicyError):
        policy.load(tmp_path)


def test_parity_catches_regressions(repo):
    pol = policy.load(repo)
    r = Router.from_repo(repo)
    bad = parity.run(repo, "gpt-4o-2024-08-06", "gpt-5.4-mini", r, pol.parity)
    assert bad.verdict == "fail"
    assert bad.schema_valid == pytest.approx(0.8)
    assert bad.label_agreement == pytest.approx(0.6)
    good = parity.run(repo, "claude-opus-4-5-20251101", "claude-opus-4-6", r, pol.parity)
    assert good.verdict == "pass"
    none = parity.run(repo, "gpt-4o-mini-2024-07-18", "gpt-5.4-nano", r, pol.parity)
    assert none.verdict == "warn" and not none.cases


def _fx(expect, old='{"n": 100, "label": "a"}'):
    return {"id": "f1", "messages": [{"role": "user", "content": "hi"}], "recorded_output": old, "expect": expect}


def test_parity_cannot_pass_silently():
    t = {"min_schema_valid": 1.0, "min_label_agreement": 0.95, "max_numeric_drift": 0.05, "max_cost_increase": 0.25}
    lost = parity._check_case(_fx({"numeric_fields": {"n": 0.01}}), '{"n": 100}', '{"label": "a"}')
    assert lost.numeric_missing
    text = parity._check_case(_fx({"format": "text", "regex": "^OK"}), "OK done", "not ok")
    assert text.regex_ok is False
    rep = parity.ParityReport("a", "b", [lost, text], 1, 1, 0, 0, t, False)
    assert rep.verdict == "fail" and any("numeric" in f for f in rep.failures) and any("regex" in f for f in rep.failures)
    unpriced = parity.ParityReport("a", "b", [parity._check_case(_fx({}), "{}", "{}")], 0, 0, 0, 0, t, False, priced=False)
    assert unpriced.verdict == "warn"


def test_strip_fences_is_opt_in():
    fenced = '```json\n{"n": 1}\n```'
    assert parity._check_case(_fx({"json_schema": {"type": "object"}}), '{"n": 1}', fenced).schema_valid_new is False
    ok = parity._check_case(_fx({"json_schema": {"type": "object"}, "strip_fences": True}), '{"n": 1}', fenced)
    assert ok.schema_valid_new and ok.fences_stripped


def test_report_is_never_pass_without_parity(repo):
    changes = registry.load()
    ps = codemod.plan(repo, scanner.scan(repo, changes), changes, policy.load(repo))
    s = report.summarize(ps, [])
    assert s["verdict"] == "not_run" and not s["ready_for_review"]
    body = report.render(changes, ps, [], dt.date(2026, 11, 25))
    assert "NOT RUN" in body.split("\n")[0] and "proposed" in body.split("\n")[0]


def test_simulated_pass_is_not_ready(repo):
    changes = registry.load()
    pol = policy.load(repo)
    ps = codemod.plan(repo, [f for f in scanner.scan(repo, changes) if f.path == "app/summarize.py"], changes, pol)
    codemod.apply(repo, ps)
    rep = parity.run(repo, "claude-opus-4-5-20251101", "claude-opus-4-6", Router.from_repo(repo), pol.parity)
    s = report.summarize(ps, [rep])
    assert s["verdict"] == "pass" and s["simulated"] and not s["ready_for_review"]
    assert "(simulated)" in report.render(changes, ps, [rep], dt.date(2026, 11, 25))


def test_parity_from_dict_recomputes_verdict(repo):
    pol = policy.load(repo)
    rep = parity.run(repo, "gpt-4o-2024-08-06", "gpt-5.4-mini", Router.from_repo(repo), pol.parity)
    d = json.loads(json.dumps(rep.to_dict()))
    d["verdict"], d["failures"] = "pass", []
    assert parity.ParityReport.from_dict(d).verdict == "fail"


def test_router_detects_non_default_endpoint(repo):
    r = Router.from_repo(repo, "live")
    assert r.is_default_endpoint("openai")
    r.config["endpoints"]["openai"] = {"base_url": "https://evil.example/v1", "key_env": "OPENAI_API_KEY"}
    assert not r.is_default_endpoint("openai")


def test_schema_subset():
    s = {"type": "object", "required": ["a"], "properties": {"a": {"type": "number"}, "b": {"enum": [1, 2]}}}
    assert parity.schema_errors({"a": 1}, s) == []
    assert parity.schema_errors({"a": True}, s)
    assert parity.schema_errors({"b": 3}, s)


def test_strip_fences():
    assert strip_fences('```json\n{"a":1}\n```') == '{"a":1}'


def test_days_left():
    c = registry.load()[0]
    assert c.days_left(dt.date(2027, 1, 13)) == 1
