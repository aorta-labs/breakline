"""CLI contract tests: every script runs from any working directory, prints JSON on stdout,
text on stderr, and uses exit codes 0 (ok), 2 (parity fail), 3 (config error)."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
TODAY = ["--today", "2026-11-25"]


def run(script, *args, cwd, env_extra=None, drop=()):
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update(env_extra or {})
    p = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)], cwd=cwd, env=env,
                       capture_output=True, text=True, timeout=120)
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        data = None
    return p.returncode, data, p.stderr


def digest(repo):
    return {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(repo.rglob("*")) if p.is_file() and ".breakline/out" not in str(p)}


@pytest.fixture
def elsewhere(tmp_path):
    d = tmp_path / "cwd"
    d.mkdir()
    return d


def test_scan_json_and_deadline_order(repo, elsewhere):
    code, d, err = run("scan.py", repo, *TODAY, cwd=elsewhere)
    assert code == 0 and d["ok"] and d["count"] == 8
    assert [g["effective_date"] for g in d["by_deadline"]] == sorted(g["effective_date"] for g in d["by_deadline"])
    assert d["by_deadline"][0]["symbol"] == "non_expiring_offline_token" and d["by_deadline"][0]["days_left"] == 37
    assert d["preflight"]["policy_file"] and d["preflight"]["fixtures"] == 13
    assert "finding(s)" in err and "{" not in err


def test_config_errors_exit_3(repo, elsewhere):
    for args in (["scan.py", elsewhere / "missing"], ["scan.py"], ["scan.py", repo, "--today", "soon"],
                 ["parity.py", repo], ["plan_patch.py", repo, "--include-ask"], ["plan_patch.py", repo, "--replace", "oops"]):
        code, d, _ = run(*args, cwd=elsewhere)
        assert code == 3 and d and d["ok"] is False, args


def test_plan_does_not_touch_sources(repo, elsewhere):
    before = digest(repo)
    code, d, _ = run("plan_patch.py", repo, *TODAY, cwd=elsewhere)
    assert code == 0 and d["mode"] == "plan" and d["proposed"] == 5 and d["applied_now"] == 0
    assert digest(repo) == before
    assert (repo / ".breakline/out/patch.diff").read_text() == d["diff"] != ""
    assert d["denied"] == ["app/billing/invoice_parser.py:8"]
    assert d["awaiting_approval"] == ["app/summarize.py:7"]
    assert {n["site"] for n in d["needs_human"]} == {"web/shopify.ts:8", "web/shopify.ts:9"}


def test_replace_flag_and_missing_replacement(repo, elsewhere):
    (repo / ".breakline/policy.yaml").write_text("rules:\n  - match: {path: '*'}\n    action: allow\n")
    code, d, _ = run("plan_patch.py", repo, *TODAY, cwd=elsewhere)
    assert d["proposed"] == 0 and {n["symbol"] for n in d["needs_replacement"]} >= {"gpt-4o-2024-08-06"}
    code, d, _ = run("plan_patch.py", repo, "--replace", "gpt-4o-2024-08-06=model-x", *TODAY, cwd=elsewhere)
    assert d["proposed"] == 3 and "model-x" in d["diff"]


def test_apply_respects_ask_and_deny(repo, elsewhere):
    run("plan_patch.py", repo, *TODAY, cwd=elsewhere)
    code, d, _ = run("plan_patch.py", repo, "--apply", *TODAY, cwd=elsewhere)
    assert code == 0 and d["applied_now"] == 4
    assert "gpt-4o-2024-08-06" not in (repo / "app/triage.py").read_text()
    assert "claude-opus-4-5-20251101" in (repo / "app/summarize.py").read_text()      # ask: untouched
    assert "gpt-4.1-2025-04-14" in (repo / "app/billing/invoice_parser.py").read_text()  # deny: untouched
    code, d, _ = run("plan_patch.py", repo, "--apply", "--include-ask", *TODAY, cwd=elsewhere)
    assert "claude-opus-4-6" in (repo / "app/summarize.py").read_text()
    assert "gpt-4.1-2025-04-14" in (repo / "app/billing/invoice_parser.py").read_text()
    res = json.loads((repo / ".breakline/out/result.json").read_text())
    assert sum(s["applied"] for s in res["sites"]) == 5  # earlier applied sites survive later runs


def test_parity_simulated_fails_and_is_labelled(repo, elsewhere):
    run("plan_patch.py", repo, *TODAY, cwd=elsewhere)
    code, d, err = run("parity.py", repo, "--simulated", cwd=elsewhere)
    assert code == 2 and d["verdict"] == "fail" and d["simulated"] and d["evidence"] is False
    assert "SIMULATED" in d["disclosure"] and "SIMULATED" in err
    bad = next(r for r in d["reports"] if r["new_model"] == "gpt-5.4-mini")
    assert bad["schema_valid"] == pytest.approx(0.8) and {x["fixture"] for x in bad["regressions"]} >= {"t01", "t04"}


def test_parity_pair_flag_passes_but_stays_simulated(repo, elsewhere):
    code, d, _ = run("parity.py", repo, "--simulated", "--pair", "claude-opus-4-5-20251101=claude-opus-4-6", cwd=elsewhere)
    assert code == 0 and d["verdict"] == "pass" and d["evidence"] is False


def test_live_needs_key_and_never_leaks_it(repo, elsewhere):
    code, d, err = run("parity.py", repo, "--live", "--pair", "gpt-4o-2024-08-06=gpt-5.4-mini", cwd=elsewhere, drop=("OPENAI_API_KEY",))
    assert code == 3 and "OPENAI_API_KEY" in d["error"]
    secret = "sk-test-secret-value-1234567890"
    (repo / ".breakline/router.yaml").write_text(json.dumps({
        "tasks": {}, "model_endpoints": {"gpt-": "evil"},
        "endpoints": {"evil": {"base_url": "http://127.0.0.1:9/v1", "key_env": "OPENAI_API_KEY"}}}))
    args = ("parity.py", repo, "--live", "--pair", "gpt-4o-2024-08-06=gpt-5.4-mini")
    code, d, err = run(*args, cwd=elsewhere, env_extra={"OPENAI_API_KEY": secret})
    assert code == 3 and "--allow-custom-endpoints" in d["error"] and secret not in json.dumps(d) + err
    code, d, err = run(*args, "--allow-custom-endpoints", cwd=elsewhere, env_extra={"OPENAI_API_KEY": secret})
    assert code == 3 and d["ok"] is False and secret not in json.dumps(d) + err   # connection refused, key not echoed


def test_report_not_run_then_fail_then_tamper_proof(repo, elsewhere):
    code, d, _ = run("plan_patch.py", repo, "--apply", *TODAY, cwd=elsewhere)
    code, d, err = run("report.py", repo, *TODAY, cwd=elsewhere)
    assert code == 0 and d["verdict"] == "not_run" and d["ready_for_review"] is False and d["open_as_draft"]
    body = Path(d["pr_body"]).read_text()
    assert "migrated" in body.split("\n")[0] and "NOT RUN" in body.split("\n")[0]
    run("parity.py", repo, "--simulated", cwd=elsewhere)
    code, d, _ = run("report.py", repo, *TODAY, cwd=elsewhere)
    assert code == 2 and d["verdict"] == "fail" and d["simulated"]
    assert "simulated" in Path(d["pr_body"]).read_text().lower()
    res_file = repo / ".breakline/out/result.json"
    res = json.loads(res_file.read_text())
    for p in res["parity"]:
        p["verdict"], p["failures"] = "pass", []
    res_file.write_text(json.dumps(res))
    code, d, _ = run("report.py", repo, *TODAY, cwd=elsewhere)
    assert code == 2 and d["verdict"] == "fail"


def test_verify_dates(repo, elsewhere):
    code, d, _ = run("verify_dates.py", *TODAY, cwd=elsewhere)
    assert code == 0 and d["unverified_count"] == 8 and d["must_check_now"] and "unverified" in d["rule"]
    code, d, _ = run("verify_dates.py", "--repo", repo, *TODAY, cwd=elsewhere)
    assert {r["symbol"] for r in d["must_check_now"]} == {
        "non_expiring_offline_token", "gpt-4.1-2025-04-14", "gpt-4o-2024-08-06", "gpt-4o-mini-2024-07-18", "claude-opus-4-5-20251101"}


def test_help_exits_zero(elsewhere):
    for s in ("scan.py", "plan_patch.py", "parity.py", "report.py", "verify_dates.py"):
        p = subprocess.run([sys.executable, str(SCRIPTS / s), "--help"], cwd=elsewhere, capture_output=True, text=True)
        assert p.returncode == 0 and "exit" in p.stdout.lower()
