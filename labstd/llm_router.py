"""Several LLM providers behind one object.

build_llm() reads the `llm` section of config/pipeline_config.yaml plus environment variables and returns an
LlmRouter. The router has the same surface as LlmClient (enabled, status(), extract(), stats, mode, ...), so the
pipeline does not care whether it talks to one provider or to a chain of them.

Chain behaviour, per file: the first provider that returns a usable answer wins. A provider that fails or answers
with nothing parseable hands over to the next one. A provider that fails with a key / credit / permission problem is
skipped for the rest of the run, the others carry on.
"""
import os

from .env import env_source
from .llm import LEGACY_KEY_VAR, MODES, PROVIDERS, LlmClient, describe_error, error_kind


class AllProvidersFailed(RuntimeError):
    def __init__(self, failures):
        self.failures = failures  # [(provider, readable error, kind)]
        super().__init__("all LLM providers failed: " + " | ".join(f"{p}: {msg[:160]}" for p, msg, _ in failures))
        self.kind = "other"  # each provider's own kind is in `failures`; this keeps describe_error from mislabelling the whole chain


def _env(name):
    v = (os.environ.get(name) or "").strip()
    return v or None


def _csv(value):
    return [x.strip() for x in str(value).split(",") if x.strip()] if value else []


def _first_env(names):
    for n in names:
        v = _env(n)
        if v:
            return n, v
    return None, None


def provider_entries(llm_cfg=None):
    """The ordered provider list as plain dicts, after the environment overrides."""
    d = llm_cfg or {}
    chosen = _csv(os.environ.get("LLM_PROVIDERS")) or _csv(os.environ.get("LLM_PROVIDER"))
    entries = [dict(e) if isinstance(e, dict) else {"name": str(e)} for e in (d.get("providers") or [])]
    if not entries and d.get("provider"):  # the old single-provider layout
        entries = [{k: d[k] for k in ("model", "base_url", "fallback_models") if d.get(k)} | {"name": d["provider"]}]
    if chosen:
        by_name = {e.get("name"): e for e in entries}
        entries = [by_name.get(n, {"name": n}) for n in chosen]
    return [e for e in entries if e.get("name")]


def build_client(entry, llm_cfg=None, first=False):
    """One LlmClient for one provider entry. Provider-specific variables win over the legacy LLM_* ones."""
    d = llm_cfg or {}
    name = str(entry["name"]).lower()
    spec = PROVIDERS.get(name)
    if spec is None:
        return LlmClient(name, entry.get("model"), None, None)  # reports itself as 'unknown provider'
    prefix = spec["prefix"]
    # key: the entry's own variable, then the provider's variables, then the legacy LLM_API_KEY for the first provider
    key_var, key = _first_env([entry["api_key_env"]]) if entry.get("api_key_env") else (None, None)
    if not key:
        key_var, key = _first_env(spec["key_vars"])
    if not key and first:
        key_var, key = _first_env([LEGACY_KEY_VAR])
    # model: provider variables, then the legacy LLM_MODEL_NAME for the first provider, then the yaml entry, then the default
    model = _first_env(spec["model_vars"])[1]
    if not model and first:
        model = _env("LLM_MODEL_NAME") or _env("LLM_MODEL")
    model = model or entry.get("model") or spec.get("default_model")
    base = _env(f"{prefix}_BASE_URL") or (_env("LLM_BASE_URL") if first else None) or entry.get("base_url")
    fb_env = _env(f"{prefix}_FALLBACK_MODELS") or (_env("LLM_FALLBACK_MODELS") if first else None)
    fallbacks = _csv(fb_env) if fb_env else list(entry.get("fallback_models") or [])
    return LlmClient(
        name, model, key, base, timeout=entry.get("timeout") or d.get("timeout_seconds") or 120, mode=_env("LLM_MODE") or d.get("mode") or "primary",
        fallback_models=fallbacks, key_source=env_source(key_var) if key_var else "not set", key_var=key_var,
        temperature=entry.get("temperature", d.get("temperature", 0)), max_tokens=entry.get("max_tokens") or d.get("max_tokens") or 4096,
    )


def build_clients(llm_cfg=None):
    return [build_client(e, llm_cfg, first=(i == 0)) for i, e in enumerate(provider_entries(llm_cfg))]


def build_llm(llm_cfg=None, only=None):
    """The LLM object the pipeline uses. `only` limits the chain to the named providers (for comparing them one by one)."""
    clients = build_clients(llm_cfg)
    if only:
        wanted = [o.lower() for o in only]
        clients = [c for c in clients if c.provider in wanted]
    return LlmRouter(clients, mode=_env("LLM_MODE") or (llm_cfg or {}).get("mode") or "primary")


class LlmRouter:
    def __init__(self, clients, mode="primary"):
        self.clients = list(clients)
        self.mode = mode if mode in MODES else "primary"
        for c in self.clients:
            c.mode = self.mode
        self.active_client = None
        self.attempts = []  # what happened in the most recent extract(): [{provider, model, ok, error}]

    # ---- the surface the pipeline relies on -------------------------------------------------------------
    @property
    def usable(self):
        """Clients that can be called right now: configured, with a key, not blocked by an earlier key / credit failure."""
        return [c for c in self.clients if c.enabled and not c.blocked_reason]

    @property
    def enabled(self):
        return self.mode != "off" and any(c.enabled for c in self.clients)

    @property
    def blocked_reason(self):
        live = [c for c in self.clients if c.enabled]
        if live and all(c.blocked_reason for c in live):
            return " | ".join(f"{c.provider}: {c.blocked_reason}" for c in live)
        return None

    @property
    def active_provider(self):
        return self.active_client.provider if self.active_client else None

    @property
    def active_model(self):
        return self.active_client.active_model if self.active_client else None

    @property
    def model(self):
        first = next((c for c in self.clients if c.enabled), self.clients[0] if self.clients else None)
        return f"{first.provider}/{first.model}" if first else None

    @property
    def stats(self):
        agg = {"calls": 0, "ok": 0, "failed": 0, "seconds": 0.0, "last_error": None, "last_kind": None, "last_model": None}
        for c in self.clients:
            for k in ("calls", "ok", "failed", "seconds"):
                agg[k] += c.stats[k]
        last = self.active_client or next((c for c in reversed(self.clients) if c.stats["last_error"]), None)
        if last:
            agg["last_model"] = last.stats["last_model"]
            agg["last_error"], agg["last_kind"] = last.stats["last_error"], last.stats["last_kind"]
        return agg

    def provider_stats(self):
        return {c.provider: dict(c.stats, model=c.model, blocked=c.blocked_reason) for c in self.clients}

    def status(self):
        if not self.clients:
            return "off: no provider configured (llm.providers in pipeline_config.yaml or LLM_PROVIDERS)"
        if self.mode == "off":
            return "off: mode is off"
        lines = [f"{c.provider}: {c.status()}" for c in self.clients]
        ready = [c for c in self.clients if c.enabled]
        head = f"ready: {len(ready)} of {len(self.clients)} provider(s), tried in this order, mode {self.mode}" if ready else "off: no provider has an API key"
        return head + "\n  " + "\n  ".join(lines)

    def extract(self, text):
        """Tries each usable provider in order. Returns the first usable extraction."""
        self.attempts = []
        failures = []
        for c in self.usable:
            try:
                out = c.extract(text)
            except Exception as e:
                msg = describe_error(e)
                failures.append((c.provider, msg, error_kind(e)))
                self.attempts.append({"provider": c.provider, "model": c.active_model or c.model, "ok": False, "error": msg[:300]})
                continue
            if out is None:
                msg = "the model answered but not with JSON that could be read"
                failures.append((c.provider, msg, "other"))
                self.attempts.append({"provider": c.provider, "model": c.active_model or c.model, "ok": False, "error": msg})
                continue
            self.active_client = c
            self.attempts.append({"provider": c.provider, "model": c.active_model or c.model, "ok": True, "error": None})
            return out
        if not failures:
            raise AllProvidersFailed([("router", "no provider is usable (missing keys, or blocked by an earlier failure)", "other")])
        raise AllProvidersFailed(failures)

    def complete(self, system, user):
        failures = []
        for c in self.usable:
            try:
                out = c.complete(system, user)
            except Exception as e:
                failures.append((c.provider, describe_error(e), error_kind(e)))
                continue
            self.active_client = c
            return out
        raise AllProvidersFailed(failures or [("router", "no provider is usable", "other")])

    def ping_all(self):
        """One tiny call per provider. Returns [{provider, model, ok, message}]. Costs a few tokens each."""
        out = []
        for c in self.clients:
            if not c.enabled:
                out.append({"provider": c.provider, "model": c.model, "ok": False, "message": c.status()})
                continue
            ok, msg = c.ping()
            out.append({"provider": c.provider, "model": c.active_model or c.model, "ok": ok, "message": msg})
        return out

    def key_origin(self):
        return "; ".join(f"{c.provider}: {c.key_origin()}" for c in self.clients if c.enabled) or "no key"

    def key_fingerprint(self):
        return ", ".join(f"{c.provider}={c.key_fingerprint()}" for c in self.clients if c.enabled) or "none"
