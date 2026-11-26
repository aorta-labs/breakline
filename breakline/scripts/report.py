#!/usr/bin/env python3
"""Render pr_body.md from result.json.

The verdict is recomputed from the stored parity cases, so editing the file cannot turn a FAIL into a PASS.
Exit 0 ok, 2 parity failed, 3 configuration error.
Usage: report.py [REPO] [--result FILE] [--out FILE]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from breakline import skillcli  # noqa: E402


def main() -> int:
    p = skillcli.parser(__doc__)
    skillcli.add_common(p, repo=False)
    p.add_argument("repo", nargs="?", help="repository (used to find .breakline/out/result.json)")
    p.add_argument("--result", help="result.json written by plan_patch.py and parity.py")
    p.add_argument("--out", help="where to write pr_body.md (default: next to result.json)")
    args = p.parse_args()

    from breakline import registry, report
    from breakline.codemod import PatchSet, SiteResult
    from breakline.parity import ParityReport
    from breakline.scanner import Finding

    if args.result:
        result_file = Path(args.result).expanduser()
    elif args.repo:
        result_file = skillcli.out_dir(args, skillcli.repo_path(args)) / "result.json"
    else:
        raise skillcli.ConfigError("pass REPO or --result")
    res = skillcli.read_json(result_file)
    if res is None:
        raise skillcli.ConfigError(f"{result_file} not found: run plan_patch.py first")

    reg = Path(args.registry) if args.registry else Path(res.get("registry") or skillcli.DEFAULT_REGISTRY)
    changes = registry.load(reg if reg.exists() else skillcli.DEFAULT_REGISTRY)
    ps = PatchSet(sites=[SiteResult(Finding(**s["finding"]), s["action"], s["status"], s.get("replacement"),
                                    s.get("reason", ""), s.get("applied", False)) for s in res["sites"]])
    parity = [ParityReport.from_dict(d) for d in res.get("parity", [])]

    body = report.render(changes, ps, parity, skillcli.today(args))
    out = Path(args.out).expanduser() if args.out else result_file.parent / "pr_body.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")

    summ = report.summarize(ps, parity)
    skillcli.emit({
        "ok": True, "pr_body": str(out), "verdict": summ["verdict"], "simulated": summ["simulated"],
        "ready_for_review": summ["ready_for_review"], "open_as_draft": not summ["ready_for_review"],
        "blockers": summ["blockers"],
    })
    skillcli.log(f"wrote {out}: parity {summ['verdict']}" + (" (simulated)" if summ["simulated"] else "")
                 + ("" if summ["ready_for_review"] else "; open the PR as a draft"))
    return skillcli.PARITY_FAIL if summ["verdict"] == "fail" else skillcli.OK


if __name__ == "__main__":
    skillcli.run(main)
