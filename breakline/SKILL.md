---
name: breakline
description: Find and safely fix calls to retiring or deprecated LLM models and APIs in a codebase, then prove the replacement behaves the same on the team's own saved prompts. Use whenever the user mentions a retiring or deprecated model, a model migration, the gpt-4o retirement, upgrading or swapping a Claude, OpenAI or Gemini model, Shopify offline tokens, "will my code break when X is retired", "our calls die in January", or "swap the model safely", even if they never say "Breakline". Scans the repo, plans a deterministic patch shown as a diff before anything is written, replays fixtures on the old and new model (parity), and writes a PR-ready report. Do NOT use for general refactors, dependency or framework upgrades with no model or API change (for example "upgrade React to 19"), or for building new LLM features.
---

# Breakline

Breakline finds the model and API calls in a repo that are about to break, plans the fix, and proves the replacement still behaves the same on the team's own prompts. Deterministic scripts decide. You draft, explain and ask. You do not decide whether a migration is safe: the parity script does.

The scripts are in `scripts/`, next to this file. Run them with `python3` (3.10 or newer, PyYAML installed; if either is missing the script says so, and you should try `python3.12`, a virtualenv with `pip install pyyaml`, or `uv run --with pyyaml python`). Each prints JSON on stdout and text on stderr. Exit code 0 is ok, 2 is a parity failure, 3 is a configuration error whose message says what to fix. Read the JSON; do not scrape the text.

Below, `$SKILL` is the directory holding this file and `$REPO` is the repository being migrated.

## Hard rules

These exist because a wrong "safe to merge" or a leaked key costs far more than a slower run.

- Never merge, and never mark a pull request ready unless `report.py` says `ready_for_review: true`.
- Never print, log or echo an API key, and never run `env`, `printenv` or `echo` on a key variable. Test for one with `[ -n "${OPENAI_API_KEY:-}" ] && echo set || echo unset`, which prints a word, not the value.
- Fixture prompts go only to the model endpoints being compared. If `parity.py` refuses because `router.yaml` redirects them, show the user the address and stop unless they approve.
- Never edit a path the policy denies. The scripts enforce this; do not work around it by hand.
- Never call a parity result "pass" if any threshold failed, if it was not run for every replaced model, or if it was simulated.
- Always say when a result is simulated: no model was called and it proves nothing.
- Never invent a model name, a replacement or a date. Say what you fetched and from where.
- Never weaken a threshold or delete a failing fixture to get a pass.

## Workflow

Work through these in order. Skipping ahead is how people ship a model swap that quietly changes behaviour.

### 1. Preflight

Confirm which repo to work on (ask if it is not clear; the current directory is a fair default). Run the scan (step 3) once and read its `preflight` block, or check directly for `$REPO/.breakline/policy.yaml` and `$REPO/.breakline/fixtures/*.jsonl`.

If the policy is missing, offer to create it from `assets/policy.example.yaml`: copy it to `$REPO/.breakline/policy.yaml` and leave `replacements` empty until the user chooses (step 4).

If fixtures are missing, parity cannot prove anything. Offer to help write 5 to 10 from real prompts: find each retiring model's call in the code, copy the prompt, and ask the user for real inputs and real outputs from logs or tickets. Follow `references/fixtures-format.md`. Do not make up `recorded_output`.

### 2. Freshness

Run `python3 $SKILL/scripts/verify_dates.py --repo $REPO`. Every record in `must_check_now` has a date the registry has not confirmed.

- With web tools: fetch each `evidence_url`, find the model or change, and compare the date. Tell the user which dates you confirmed, which differ (use the provider's date and say the registry is out of date) and which you could not confirm.
- Without web tools: say so.

Until a date is confirmed, write "the registry says 2027-01-14 (not yet confirmed)". Never state it as fact. If `registry_age_days` is large, mention that the registry may be missing newer retirements.

### 3. Scan

Run `python3 $SKILL/scripts/scan.py $REPO`. Summarize `by_deadline`, soonest first: the date, days left, whether the date is verified, and each `file:line`. Mention `limits`: only exact model ids are matched. Also search for the same model family under other spellings (an undated alias, a provider prefix like `openai/...`, an id built from parts) and list anything you find as "possible, not confirmed". If the user asked about a provider or API the registry has no record for, say the registry does not cover it; do not claim the repo is clean.

If `count` is 0, say what was and was not checked and stop.

### 4. Choose replacements

Ask the user which model replaces each retiring one, unless `replacement` is already set from `policy.yaml`. You may suggest options only from sources you fetched in this session: the provider's deprecation notice (it usually names a replacement) or its current models page. Cite the URL. Never fill a replacement from memory. A suggestion is a question: wait for the answer, and do not plan with a replacement the user has not chosen. "The new one", "the latest" or "something newer" is not a choice, even when the provider's notice names exactly one replacement: name it, cite it, ask "use this one?" and end your turn without passing `--replace`. Until the user answers, `plan_patch.py` lists the site under `needs_replacement`, which is the right result.

Pass the user's choices with `--replace OLD=NEW`, or write them to `policy.yaml` if they want them kept.

### 5. Plan

Run `python3 $SKILL/scripts/plan_patch.py $REPO --replace OLD=NEW ...`. This writes `patch.diff` and `result.json` under `$REPO/.breakline/out/` and changes no source file.

Show the user the diff from the JSON `diff` field in full. Then summarize:

- Proposed sites by policy: `allow` (will be written), `awaiting_approval` (policy says `ask`).
- `denied`: reported only, never touched.
- `needs_human`: auth or API changes with no safe automatic fix (for example Shopify offline tokens). Explain the manual change, citing the source.
- `needs_replacement`: ask for a choice.

### 6. Prove

Parity needs fixtures. The pairs come from the plan.

- If the provider keys for the pairs are set (check names only), fixtures exist and the user agrees to spend tokens (about two calls per fixture per pair; say the count), run `python3 $SKILL/scripts/parity.py $REPO --live`.
- Otherwise say that live parity cannot run yet and what it needs (the key names, and fixtures for every old model). If `preflight.sim_profiles` is true, this is a demo repo: run `parity.py $REPO --simulated` so the user sees how a report reads, and say plainly that no model was called and it proves nothing about the new model. Without sim profiles a simulated run only echoes the recordings, so do not run it unless the user asks.

Show the table: schema-valid, label agreement, numeric drift, cost, latency, fixtures replayed, with thresholds. Use the JSON, not memory. Explain every regression listed.

On a FAIL, propose concrete fixes and then replay. `references/parity-checks.md` maps each symptom to a fix: tolerant JSON parsing in the app (then set `expect.strip_fences` on the matching fixtures), pinning the label set in the prompt (and mirroring that in the fixture messages), or choosing another replacement. Make code changes only with the user's approval, then re-run `parity.py`. A fix to the app's parser does not change what the model outputs, so a fence failure only clears once the fixtures reflect how the app now parses.

Exit 3 means something to fix, not a verdict: read `error`. If it says `router.yaml` points somewhere unexpected, follow the hard rule above.

### 7. Apply and report

Apply only after the user explicitly approves the diff.

1. `python3 $SKILL/scripts/plan_patch.py $REPO --apply --replace OLD=NEW ...` writes the `allow` sites.
2. For `ask` sites, list them, get approval, then add `--include-ask`.
3. Suggest running the project's own tests, and offer to if you can.
4. `python3 $SKILL/scripts/report.py $REPO` writes `pr_body.md` and prints `ready_for_review`, `open_as_draft` and `blockers`. Do not hand-edit `pr_body.md`: it is generated from the stored results so that its verdict can be trusted. Put extra notes in your message or in a separate PR comment.

If the user wants a pull request and `gh` is available: confirm the branch name and that they want it pushed (it is visible to others), create a branch, commit, push, and run `gh pr create --body-file ...`. Add `--draft` unless `ready_for_review` is true. Do not merge. Show the user the PR link and the blockers.

## Final message

Keep it short and factual: what was found (by deadline), what changed or is proposed, the parity result with the number of fixtures and whether it was live or simulated, what is left for a human, and the next step. State clearly if parity was simulated, did not run, or failed.

## References

- `references/policy-format.md`: `policy.yaml`, allow/ask/deny, thresholds.
- `references/fixtures-format.md`: fixtures, prices, router and simulator files; how to write good fixtures.
- `references/parity-checks.md`: what each check means and how to read a FAIL.
- `references/registry.json`: the change records the scanner uses. Each has a source URL; records marked `unverified` need step 2.
- `assets/policy.example.yaml`: a policy to copy.
