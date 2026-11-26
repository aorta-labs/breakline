#!/usr/bin/env python3
"""Scan a repo for calls to retiring models and deprecated APIs.

Read-only. Prints JSON findings on stdout, a short summary on stderr.
Usage: scan.py REPO [--registry FILE] [--policy FILE] [--today YYYY-MM-DD]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from breakline import skillcli  # noqa: E402


def main() -> int:
    p = skillcli.parser(__doc__)
    skillcli.add_common(p)
    p.add_argument("--policy", help="policy file (default: REPO/.breakline/policy.yaml)")
    p.add_argument("--fixtures", help="fixtures directory (default: REPO/.breakline/fixtures)")
    args = p.parse_args()

    from breakline import parity, policy, registry, scanner

    repo, today = skillcli.repo_path(args), skillcli.today(args)
    reg = skillcli.registry_path(args)
    changes = registry.load(reg)
    by_id = {c.id: c for c in changes}
    pol = policy.load(repo, args.policy)
    findings = scanner.scan(repo, changes)

    rows = []
    for f in findings:
        c = by_id[f.change_id]
        rows.append({**f.to_dict(), "provider": c.provider, "kind": c.kind, "effective_date": c.effective_date.isoformat(),
                     "days_left": c.days_left(today), "date_status": c.status, "evidence_url": c.evidence_url,
                     "policy_action": pol.decide(f.path, c.provider, c.id), "replacement": pol.replacements.get(c.symbol)})
    rows.sort(key=lambda r: (r["effective_date"], r["path"], r["line"]))

    groups: dict[str, dict] = {}
    for r in rows:
        g = groups.setdefault(r["change_id"], {
            "change_id": r["change_id"], "symbol": r["symbol"], "provider": r["provider"], "kind": r["kind"],
            "effective_date": r["effective_date"], "days_left": r["days_left"], "date_status": r["date_status"],
            "evidence_url": r["evidence_url"], "replacement": r["replacement"], "locations": []})
        g["locations"].append(f"{r['path']}:{r['line']}")
    by_deadline = sorted(groups.values(), key=lambda g: g["effective_date"])

    fixtures_dir = Path(args.fixtures) if args.fixtures else repo / ".breakline" / "fixtures"
    fixtures = parity.load_fixtures(repo, fixtures_dir) if fixtures_dir.is_dir() else []
    policy_file = Path(args.policy) if args.policy else repo / ".breakline" / "policy.yaml"
    skillcli.emit({
        "ok": True, "repo": str(repo), "today": today.isoformat(), "registry": str(reg), **skillcli.registry_meta(reg),
        "preflight": {"policy_file": policy_file.exists(), "fixtures": len(fixtures),
                      "sim_profiles": (repo / ".breakline" / "sim_profiles.yaml").exists(),
                      "fixture_models": sorted({f.get("model", "(any)") for f in fixtures})},
        "count": len(rows), "by_deadline": by_deadline, "findings": rows,
        "limits": "Matches exact model IDs in code and config. Aliases, undated IDs and provider-prefixed IDs are not found.",
    })
    skillcli.log(f"{len(rows)} finding(s) in {repo}")
    for g in by_deadline:
        tag = "" if g["date_status"] == "verified" else " [date unverified]"
        skillcli.log(f"  {g['effective_date']} ({g['days_left']}d) {g['symbol']}{tag}: {', '.join(g['locations'])}")
    return 0


if __name__ == "__main__":
    skillcli.run(main)
