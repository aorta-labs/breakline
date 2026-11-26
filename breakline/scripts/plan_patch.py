#!/usr/bin/env python3
"""Plan deterministic model-ID fixes; apply them only with --apply.

Writes patch.diff and result.json to --out (default REPO/.breakline/out).
No source file changes unless --apply is given. --apply writes `allow` sites
only; `ask` sites also need --include-ask; `deny` sites are never touched.
Usage: plan_patch.py REPO [--replace OLD=NEW ...] [--apply [--include-ask]]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from breakline import skillcli  # noqa: E402


def _ser(s) -> dict:
    return {"finding": s.finding.to_dict(), "action": s.action, "status": s.status,
            "replacement": s.replacement, "reason": s.reason, "applied": s.applied}


def main() -> int:
    p = skillcli.parser(__doc__)
    skillcli.add_common(p)
    p.add_argument("--policy", help="policy file (default: REPO/.breakline/policy.yaml)")
    p.add_argument("--replace", action="append", metavar="OLD=NEW",
                   help="replacement chosen by the user; overrides policy.yaml; repeatable")
    p.add_argument("--out", help="output directory (default: REPO/.breakline/out)")
    p.add_argument("--apply", action="store_true", help="write the patch to the repo (user approval required)")
    p.add_argument("--include-ask", action="store_true", help="with --apply, also write sites the policy marks `ask`")
    args = p.parse_args()
    if args.include_ask and not args.apply:
        raise skillcli.ConfigError("--include-ask only works together with --apply")

    from breakline import codemod, policy, registry, scanner

    repo, today = skillcli.repo_path(args), skillcli.today(args)
    reg = skillcli.registry_path(args)
    changes = registry.load(reg)
    pol = policy.load(repo, args.policy)
    pol.replacements.update(skillcli.parse_pairs(args.replace, "--replace"))
    findings = scanner.scan(repo, changes)
    ps = codemod.plan(repo, findings, changes, pol)

    changed: list[str] = []
    if args.apply:
        allowed = {"allow"} | ({"ask"} if args.include_ask else set())
        subset = [s.finding for s in ps.patched if s.action in allowed]
        ap = codemod.plan(repo, subset, changes, pol)
        codemod.apply(repo, ap)
        done = {(s.finding.path, s.finding.line, s.finding.change_id, s.replacement) for s in ap.patched}
        for s in ps.patched:
            s.applied = (s.finding.path, s.finding.line, s.finding.change_id, s.replacement) in done
        changed = sorted(ap.new_text)

    out = skillcli.out_dir(args, repo)
    result_file = out / "result.json"
    prev = skillcli.read_json(result_file) or {}
    if prev.get("repo") != str(repo):
        prev = {}
    sites = [_ser(s) for s in ps.sites]
    seen = {skillcli.site_key(s) for s in sites}
    kept = [s for s in prev.get("sites", []) if s.get("applied") and skillcli.site_key(s) not in seen]
    sites = kept + sites
    pairs = {(s["finding"]["symbol"], s["replacement"]) for s in sites if s["status"] == "patched"}
    parity_kept = [d for d in prev.get("parity", []) if (d["old_model"], d["new_model"]) in pairs]
    if args.apply and any(d.get("verdict") == "fail" for d in parity_kept):
        skillcli.log("warning: the last parity run FAILED for at least one pair; this will be reported as a draft.")

    result = {"version": 1, "repo": str(repo), "today": today.isoformat(), "registry": str(reg),
              "applied": any(s["applied"] for s in sites), "findings": [f.to_dict() for f in findings],
              "sites": sites, "parity": parity_kept}
    out.mkdir(parents=True, exist_ok=True)
    (out / "patch.diff").write_text(ps.diff, encoding="utf-8")
    skillcli.write_json(result_file, result)

    by_status: dict[str, int] = {}
    for s in ps.sites:
        by_status[s.status] = by_status.get(s.status, 0) + 1
    need: dict[str, dict] = {}
    for s in ps.sites:
        if s.status == "no_replacement":
            n = need.setdefault(s.finding.symbol, {"symbol": s.finding.symbol, "sites": 0})
            n["sites"] += 1
    skillcli.emit({
        "ok": True, "mode": "apply" if args.apply else "plan", "patch_file": str(out / "patch.diff"), "result_file": str(result_file),
        "proposed": len(ps.patched), "applied_now": sum(1 for s in ps.patched if s.applied), "files_changed": changed,
        "by_status": by_status, "needs_replacement": list(need.values()),
        "awaiting_approval": [f"{s.finding.path}:{s.finding.line}" for s in ps.patched if s.action == "ask" and not s.applied],
        "denied": [f"{s.finding.path}:{s.finding.line}" for s in ps.sites if s.status == "skipped_deny"],
        "needs_human": [{"site": f"{s.finding.path}:{s.finding.line}", "symbol": s.finding.symbol, "reason": s.reason}
                        for s in ps.sites if s.status == "needs_human"],
        "pairs": [{"old": a, "new": b} for a, b in sorted(pairs)], "diff": ps.diff,
    })
    skillcli.log(("applied" if args.apply else "planned") + f": {len(ps.patched)} site(s), {len(changed)} file(s) written, "
                 f"{sum(1 for s in ps.sites if s.status == 'skipped_deny')} denied, {len(need)} symbol(s) need a replacement")
    return 0


if __name__ == "__main__":
    skillcli.run(main)
