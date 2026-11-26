# Fixtures, prices and router files

All of these live in `<repo>/.breakline/`. Each has a flag to point elsewhere.

## Fixtures: `.breakline/fixtures/*.jsonl`

One JSON object per line. A fixture is a real prompt the app sends, plus a description of what a correct answer looks like.

```json
{"id": "t01", "model": "<old-model-id>", "messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}], "recorded_output": "{\"intent\": \"refund_request\", \"amount\": 1499}", "expect": {"json_schema": {"type": "object", "required": ["intent", "amount"], "properties": {"intent": {"type": "string", "enum": ["refund_request", "billing_question"]}, "amount": {"type": "number"}}}, "label_field": "intent", "numeric_fields": {"amount": 0.01}}}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Shown next to each regression. Keep it stable. |
| `messages` | yes | The exact messages the app sends, system prompt included. |
| `model` | no | The old model this fixture belongs to. Without it the fixture runs for every old model. |
| `recorded_output` | for `--simulated` | What the old model returned. It must come from a real run or a log, never from guesswork. `--live` calls the old model again. |
| `expect.json_schema` | no | Supported keywords: `type`, `required`, `properties`, `enum`, `items`. |
| `expect.label_field` | no | A field whose value must equal the old output's value. |
| `expect.numeric_fields` | no | `{field: tolerance}`, tolerance as a relative change (`0.01` is 1%). A numeric field that is missing or not a number in the new output fails. |
| `expect.regex` | no | Must match the new output. A failed regex fails parity. |
| `expect.format` | no | `"text"` skips JSON parsing, for free-text answers. Use it with `regex`, or nothing is checked. |
| `expect.strip_fences` | no | `true` removes a surrounding code fence before checking. Set it only when the app's own parser does the same. The report says so. |

## Writing good fixtures

Aim for 5 to 10 per model, and more for anything that drives money or routing.

1. Find each model call in the code, copy its prompt template, and fill it with real inputs: from logs, support tickets, test data or the user.
2. Cover the ordinary case, the edge cases that exist in production (empty input, very long input, non-English text, ambiguous cases) and one input the model got wrong in the past.
3. Check the field the app branches on (`label_field`) and every number the app uses (`numeric_fields`).
4. Do not write `recorded_output` yourself. If the user has no real outputs, record them by running the old model once, or leave the fixture for `--live`.
5. Keep fixtures free of secrets and personal data. They are sent to the model endpoints during `--live`.

A model with no fixtures has nothing proven: the verdict is a warning, never a pass.

## Prices: `.breakline/prices.yaml`

```yaml
<model-id>: {input_per_1m: 0.00, output_per_1m: 0.00}   # USD per 1M tokens
```

Copy current numbers from the provider's pricing page. If either model is missing, the cost check is skipped and the verdict is a warning.

## Router: `.breakline/router.yaml` (optional)

Maps model-id prefixes to endpoints. The built-in defaults cover OpenAI, Anthropic's OpenAI-compatible endpoint, OpenRouter and local Ollama, with keys read from `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` and `OPENROUTER_API_KEY`.

```yaml
model_endpoints: {"gpt-": openai, "claude-": anthropic}
endpoints:
  openai: {base_url: "https://api.openai.com/v1", key_env: OPENAI_API_KEY}
```

If this file changes an endpoint's `base_url` or `key_env` from the built-in value, `parity.py --live` refuses to run: fixture prompts and a key would go to that address. Show the user the address and run with `--allow-custom-endpoints` only if they approve. Models from other providers can be reached through an OpenAI-compatible endpoint such as OpenRouter.

## Simulator profiles: `.breakline/sim_profiles.yaml` (demo only)

Describes how a model's output differs from the recording, for `--simulated`: `wrap_json_fences`, `fence_on`, `label_map`, `number_scale`, `output_token_scale`, `latency_ms`. A model with no profile returns the recorded output unchanged, so a simulated run with no profiles always passes and proves nothing.
