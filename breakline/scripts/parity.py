#!/usr/bin/env python3
"""Replay the team's fixtures on the old and new model and compare.

--simulated is an offline demo: no model is called and the result proves nothing.
--live sends fixture prompts to the model endpoints, using keys from the environment.
Exit 0 ok, 2 parity failed, 3 configuration error.
Usage: parity.py REPO (--simulated | --live) [--pair OLD=NEW ...] [--result FILE]
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from breakline import skillcli  # noqa: E402

DISCLOSURE = {
    True: "SIMULATED: no model was called. The simulator replays recorded outputs, optionally altered by sim_profiles.yaml. "
          "This result is a demo and is not evidence that the new model behaves the same.",
    False: "LIVE: fixture prompts were sent to the endpoints listed in `endpoints`, with keys read from the environment.",
}


def main() -> int:
    p = skillcli.parser(__doc__)
    skillcli.add_common(p)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--simulated", action="store_true", help="offline demo, no network")
    mode.add_argument("--live", action="store_true", help="call the real model endpoints (spends tokens)")
    p.add_argument("--pair", action="append", metavar="OLD=NEW", help="model pair to compare; default: pairs in result.json")
    p.add_argument("--result", help="result.json from plan_patch.py (default: REPO/.breakline/out/result.json)")
    p.add_argument("--policy", help="policy file (thresholds come from its `parity:` block)")
    p.add_argument("--fixtures", help="fixtures directory (default: REPO/.breakline/fixtures)")
    p.add_argument("--prices", help="prices.yaml (default: REPO/.breakline/prices.yaml)")
    p.add_argument("--router", help="router.yaml (default: REPO/.breakline/router.yaml)")
    p.add_argument("--sim-profiles", help="sim_profiles.yaml for --simulated")
    p.add_argument("--allow-custom-endpoints", action="store_true",
                   help="allow a router.yaml that redirects a model to a non-default endpoint; only with the user's approval")
    args = p.parse_args()

    from breakline import codemod, parity, policy, registry, scanner
    from breakline.router import Router

    repo = skillcli.repo_path(args)
    pol = policy.load(repo, args.policy)
    result_file = Path(args.result) if args.result else skillcli.out_dir(args, repo) / "result.json"
    prev = skillcli.read_json(result_file)
    if prev and prev.get("repo") != str(repo):
        prev = None

    if args.pair:
        pairs = sorted(skillcli.parse_pairs(args.pair, "--pair").items())
    elif prev:
        pairs = sorted({(s["finding"]["symbol"], s["replacement"]) for s in prev["sites"] if s["status"] == "patched"})
    else:
        changes = registry.load(skillcli.registry_path(args))
        ps = codemod.plan(repo, scanner.scan(repo, changes), changes, pol)
        pairs = sorted({(s.finding.symbol, s.replacement) for s in ps.patched})
    if not pairs:
        raise skillcli.ConfigError("no replacement pairs: run plan_patch.py first, set replacements in policy.yaml, or pass --pair OLD=NEW")

    router = Router.from_repo(repo, "live" if args.live else "simulated", args.router, args.sim_profiles)
    endpoints = []
    if args.live:
        for model in sorted({m for pair in pairs for m in pair}):
            name = router.endpoint_name(model)
            info = router.endpoint_info(name)
            if not router.is_default_endpoint(name) and not args.allow_custom_endpoints:
                raise skillcli.ConfigError(
                    f"router.yaml sends {model!r} to a non-default endpoint {info.get('base_url')!r} "
                    f"(key from ${info.get('key_env')}). Fixture prompts and that key would go there. "
                    "Ask the user; re-run with --allow-custom-endpoints only if they approve.")
            env = info.get("key_env")
            if env and not os.environ.get(env):
                raise skillcli.ConfigError(f"${env} is not set. Export it, or use --simulated for a demo.")
            endpoints.append({"model": model, "endpoint": name, "base_url": info["base_url"], "key_env": env})

    reports = [parity.run(repo, old, new, router, pol.parity, args.fixtures, args.prices) for old, new in pairs]

    base = prev or {"version": 1, "repo": str(repo), "registry": str(skillcli.registry_path(args)),
                    "applied": False, "findings": [], "sites": [], "parity": []}
    redone = {(r.old_model, r.new_model) for r in reports}
    base["parity"] = [d for d in base.get("parity", []) if (d["old_model"], d["new_model"]) not in redone] + [r.to_dict() for r in reports]
    skillcli.write_json(result_file, base)

    simulated = not args.live
    overall = "fail" if any(r.verdict == "fail" for r in reports) else "warn" if any(r.verdict == "warn" for r in reports) else "pass"
    skillcli.emit({
        "ok": True, "mode": "live" if args.live else "simulated", "simulated": simulated, "evidence": not simulated,
        "disclosure": DISCLOSURE[simulated], "verdict": overall, "endpoints": endpoints, "result_file": str(result_file),
        "reports": [{"old_model": r.old_model, "new_model": r.new_model, "verdict": r.verdict,
                     "failures": r.failures, "warnings": r.warnings, "fixtures": len(r.cases),
                     "schema_valid": r.schema_valid, "label_agreement": r.label_agreement,
                     "max_numeric_drift": r.max_numeric_drift, "cost_delta": r.cost_delta,
                     "cost_old": r.cost_old, "cost_new": r.cost_new,
                     "latency_old_ms": r.latency_old_ms, "latency_new_ms": r.latency_new_ms,
                     "thresholds": r.thresholds,
                     "regressions": [{"fixture": c.fixture_id, "notes": c.notes} for c in r.cases if c.notes]} for r in reports],
    })
    skillcli.log(DISCLOSURE[simulated])
    for r in reports:
        skillcli.log(f"{r.old_model} -> {r.new_model}: {r.verdict.upper()}  ({len(r.cases)} fixtures)")
        for f in r.failures:
            skillcli.log(f"  FAIL {f}")
        for w in r.warnings:
            skillcli.log(f"  warn {w}")
    return skillcli.PARITY_FAIL if overall == "fail" else skillcli.OK


if __name__ == "__main__":
    skillcli.run(main)
