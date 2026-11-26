# `.breakline/policy.yaml`

The policy lives in the customer's repo, so it is reviewed like any other code. Pass another file with `--policy`.

```yaml
replacements:                     # old model id -> new model id, chosen by the team
  <old-model-id>: <new-model-id>

rules:                            # first match wins; no match means `ask`
  - match: {path: "billing/*"}
    action: deny
  - match: {provider: anthropic}
    action: ask
  - match: {path: "*"}
    action: allow

parity:
  min_schema_valid: 1.0
  min_label_agreement: 0.95
  max_numeric_drift: 0.05
  max_cost_increase: 0.25
```

A complete starting point is `assets/policy.example.yaml`.

## `replacements`

Maps each retiring model id to the id the team wants to test. Use exact ids. The script rejects anything that is not a plain model id (letters, digits, `. _ : / @ -`), because the value is written into source code. `--replace OLD=NEW` on `plan_patch.py` overrides the file for one run.

Never fill this in from memory. Take the replacement from the user, or from the provider's current models page fetched in this session.

## `rules`

Each rule has a `match` and an `action`. All keys in `match` must hold for the rule to apply.

| Match key | Meaning |
| --- | --- |
| `path` | Shell-style pattern against the file path relative to the repo. `*` also matches `/`, so `billing/*` covers every file below `billing/`. `**` behaves like `*`. |
| `provider` | The registry record's provider, for example `openai`, `anthropic`, `shopify`. |
| `change` | A registry record id, to target one retirement. |

| Action | What the skill does |
| --- | --- |
| `allow` | Includes the site in the plan. `plan_patch.py --apply` writes it. |
| `ask` | Includes the site in the plan, but `--apply` leaves it alone until you also pass `--include-ask` after the user approves it. |
| `deny` | Reports the site and never edits it, whatever flags are passed. |

An unknown action is a configuration error (exit 3).

## `parity`

Thresholds for the parity verdict. Defaults are shown above. Lower a threshold only when the user asks, and say so in the report. See `parity-checks.md` for what each one measures.

## Examples

Auto-fix tests, keep finance code untouched, review everything from one provider:

```yaml
rules:
  - {match: {path: "tests/*"}, action: allow}
  - {match: {path: "app/billing/*"}, action: deny}
  - {match: {provider: openai}, action: ask}
```

Treat one retirement differently from the rest:

```yaml
rules:
  - {match: {change: shopify-offline-token-non-expiring}, action: ask}
  - {match: {path: "*"}, action: allow}
```
