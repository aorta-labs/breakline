"""Swappable Model Router.

Business logic never imports a vendor SDK. It calls complete(task, messages)
for Breakline's own work, or call_model(model, messages) to replay a
customer's model during parity.

Providers:
  simulated  - deterministic offline stand-in. NOT a real model.
  openai     - any OpenAI-compatible endpoint (OpenAI, Anthropic's compatibility
               endpoint, OpenRouter, local Ollama or LM Studio)
"""
from __future__ import annotations

import copy
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_ROUTER = {
    "tasks": {
        "extract_change": ["ollama/qwen2.5-coder:7b", "openrouter/meta-llama/llama-3.3-70b-instruct:free"],
        "write_patch": ["ollama/qwen2.5-coder:14b", "openrouter/qwen/qwen-2.5-coder-32b-instruct:free"],
        "judge": ["openrouter/meta-llama/llama-3.3-70b-instruct:free"],
    },
    "endpoints": {
        "ollama": {"base_url": "http://localhost:11434/v1", "key_env": None},
        "openrouter": {"base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY"},
        "openai": {"base_url": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY"},
        # Anthropic's OpenAI-SDK-compatible endpoint
        "anthropic": {"base_url": "https://api.anthropic.com/v1", "key_env": "ANTHROPIC_API_KEY"},
    },
    # which endpoint replays a customer model, by model-id prefix
    "model_endpoints": {"claude-": "anthropic", "gpt-": "openai", "o": "openai", "whisper": "openai"},
}


class RouterError(RuntimeError):
    pass


@dataclass
class Completion:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


def _approx_tokens(s: str) -> int:
    return max(1, len(s) // 4)


class SimulatedProvider:
    """Deterministic stand-in for a model. NOT a real model.

    Profiles live in sim_profiles.yaml and describe how a model's output differs
    from the recorded baseline: wrap_json_fences, fence_on, label_map,
    number_scale, output_token_scale, latency_ms. With no profile a model
    simply echoes the recorded output, so parity always passes. That proves nothing.
    """

    def __init__(self, profiles: dict):
        self.profiles = profiles

    def call(self, model: str, messages: list[dict], fixture: dict | None = None) -> Completion:
        prof = self.profiles.get(model, {})
        base = (fixture or {}).get("recorded_output", "")
        text = base
        try:
            obj = json.loads(base)
            for k, v in list(obj.items()):
                if isinstance(v, str) and v in prof.get("label_map", {}):
                    obj[k] = prof["label_map"][v]
                if isinstance(v, (int, float)) and not isinstance(v, bool) and "number_scale" in prof:
                    obj[k] = round(v * prof["number_scale"], 2)
            text = json.dumps(obj)
            fid = (fixture or {}).get("id")
            if prof.get("wrap_json_fences") and fid in prof.get("fence_on", [fid]):
                text = f"```json\n{text}\n```"
        except (json.JSONDecodeError, AttributeError):
            pass
        prompt = "".join(m["content"] for m in messages)
        out_scale = prof.get("output_token_scale", 1.0)
        return Completion(text, model, _approx_tokens(prompt), int(_approx_tokens(text) * out_scale),
                          float(prof.get("latency_ms", 400)))


class OpenAICompatibleProvider:
    def __init__(self, base_url: str, api_key: str | None):
        self.base_url, self.api_key = base_url.rstrip("/"), api_key

    def call(self, model: str, messages: list[dict], fixture: dict | None = None) -> Completion:
        body = json.dumps({"model": model, "messages": messages, "temperature": 0}).encode()
        req = urllib.request.Request(f"{self.base_url}/chat/completions", data=body,
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            # The response body can echo request details, so report only the status.
            raise RouterError(f"{self.base_url} returned HTTP {e.code} for model {model!r}") from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            raise RouterError(f"{self.base_url} call for model {model!r} failed: {type(e).__name__}") from None
        ms = (time.perf_counter() - t0) * 1000
        usage = data.get("usage", {})
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise RouterError(f"{self.base_url} returned an unexpected response for model {model!r}") from None
        return Completion(text, model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), ms)


class Router:
    def __init__(self, config: dict | None = None, sim_profiles: dict | None = None, mode: str = "simulated"):
        self.config = config or copy.deepcopy(DEFAULT_ROUTER)
        self.mode = mode
        self.sim = SimulatedProvider(sim_profiles or {})

    @classmethod
    def from_repo(cls, repo: Path | str, mode: str = "simulated",
                  router_path: Path | str | None = None, sim_path: Path | str | None = None) -> "Router":
        repo = Path(repo)
        cfg = copy.deepcopy(DEFAULT_ROUTER)
        rp = Path(router_path) if router_path else repo / ".breakline" / "router.yaml"
        if rp.exists():
            cfg = yaml.safe_load(rp.read_text(encoding="utf-8")) or cfg
        sp = Path(sim_path) if sim_path else repo / ".breakline" / "sim_profiles.yaml"
        sims = (yaml.safe_load(sp.read_text(encoding="utf-8")) or {}) if sp.exists() else {}
        return cls(cfg, sims, mode)

    def endpoint_name(self, model: str) -> str:
        table = self.config.get("model_endpoints", DEFAULT_ROUTER["model_endpoints"])
        return next((ep for prefix, ep in table.items() if model.startswith(prefix)), "openrouter")

    def endpoint_info(self, name: str) -> dict:
        try:
            return self.config["endpoints"][name]
        except KeyError:
            raise RouterError(f"router config has no endpoint named {name!r}") from None

    def is_default_endpoint(self, name: str) -> bool:
        """True if the endpoint is exactly the one shipped with the skill."""
        return self.config.get("endpoints", {}).get(name) == DEFAULT_ROUTER["endpoints"].get(name)

    def _endpoint(self, name: str) -> OpenAICompatibleProvider:
        ep = self.endpoint_info(name)
        key = os.environ.get(ep["key_env"]) if ep.get("key_env") else None
        return OpenAICompatibleProvider(ep["base_url"], key)

    def call_model(self, model: str, messages: list[dict], fixture: dict | None = None) -> Completion:
        """Replay a *customer's* model (old or new) for parity."""
        if self.mode == "simulated":
            return self.sim.call(model, messages, fixture)
        return self._endpoint(self.endpoint_name(model)).call(model, messages, fixture)

    def complete(self, task: str, messages: list[dict]) -> Completion:
        """Breakline's own LLM work, with ordered fallbacks."""
        last = None
        for target in self.config["tasks"][task]:
            ep, _, model = target.partition("/")
            try:
                return self._endpoint(ep).call(model, messages)
            except Exception as e:  # fall through to next model
                last = e
        raise RouterError(f"all models failed for task {task}: {last}")


def strip_fences(text: str) -> str:
    m = re.fullmatch(r"\s*```(?:json)?\s*(.*?)\s*```\s*", text, re.S)
    return m.group(1) if m else text
