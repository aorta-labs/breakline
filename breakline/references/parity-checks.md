# Parity checks

Parity replays each fixture on the old and the new model and compares the outputs with fixed rules. No model judges another model. The verdict comes from numbers and thresholds, and `report.py` recomputes it from the stored cases.

## The checks

| Check | Measures | Fails when |
| --- | --- | --- |
| Schema-valid | Share of new outputs that parse as JSON and satisfy `json_schema` | below `min_schema_valid` (default 100%) |
| Label agreement | Share of fixtures where `label_field` equals the old value | below `min_label_agreement` (default 95%) |
| Numeric drift | Largest relative change in any `numeric_fields` value | above `max_numeric_drift` (default 5%), or the field is missing or not a number |
| Regex | `expect.regex` against the new output | any fixture does not match |
| Cost | Token cost of the fixtures on the new model vs the old, using `prices.yaml` | above `max_cost_increase` (default +25%) |
| Latency | Mean response time, old vs new | never fails; informational |

## Verdicts

| Verdict | Meaning |
| --- | --- |
| `fail` | At least one threshold failed. Not safe to merge. Exit code 2. |
| `warn` | No threshold failed, but something was not checked or costs more: no fixtures, a missing price, an expectation that could not be evaluated, or a cost increase. |
| `pass` | Every check that ran passed, and each check was possible. |
| `not_run` | (Report only.) Parity has not run for a replaced model. Never shown as a pass. |

A `pass` from `--simulated` is labelled "(simulated)" everywhere and never makes a PR ready for review.

## Reading a FAIL

| Symptom in the regressions list | Likely cause | Fix |
| --- | --- | --- |
| `not JSON ... (output wrapped in ``` fences)` | The new model adds a code fence | Make the app's parser strip fences, then add `"strip_fences": true` to those fixtures. Or tell the model to return raw JSON, in the app prompt and in the fixture messages. |
| `intent: 'a' -> 'b'` | The new model labels the same input differently | Pin the label set and definitions in the prompt, update the fixture messages to match, replay. If it persists, the replacement is a poor fit: try another. |
| `$.field: expected number, got str` | Numbers returned as strings | Use a schema-enforced output mode in the app, or coerce in the parser, then replay. |
| `numeric field ... missing` | The key was renamed or dropped | Pin the key names in the prompt. |
| `output does not match /.../` | Format changed | Compare the two outputs; adjust the prompt or the regex only if the old regex was too strict. |
| `cost +NN%` | The new model is pricier | A product decision. Show the numbers and let the user decide; do not raise the threshold yourself. |
| a request error (exit 3) | Wrong model id, missing key or no access | Check the id on the provider's models page and the key's access. |

A parser change alone does not turn a failing parity run into a passing one, because parity checks the model's raw output. The fixtures and the prompt must reflect what the app really does.

Never loosen a threshold or delete a failing fixture to get a pass. If the user wants that, record it in the report.

## Limits

- The checks are structural. They do not say the new answers are as good, only that the app's contract holds on the fixtures supplied.
- A small fixture set can miss regressions. Say how many fixtures ran.
- Models vary run to run even at temperature 0. A single borderline label flip may be noise: run again before concluding.
- The registry matches exact model ids. Aliases, undated ids and provider-prefixed ids need a separate search.
