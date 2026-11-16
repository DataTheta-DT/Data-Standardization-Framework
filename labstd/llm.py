import json
import os
import re
import time
import urllib.error
import urllib.request

from .env import env_source
from .parsers import NOT_TEST_RE, NOT_UNITS, make_row, norm_sex, split_title
from .structure import to_iso

CHUNK_CHARS = 9000
MODES = ("primary", "fallback", "assist", "off")

SYSTEM_PROMPT = """You are a data extraction engine for laboratory reports.
The user message contains text that PyMuPDF extracted from a report. In that text, " | " separates table cells that were on the same line of the page. It is a layout aid and is not part of any value.

Task: find every individual laboratory test result, plus the patient and date details of the report, and return them as ONE JSON object in the exact schema given.

Rules:
1. Copy every string exactly as it is written in the text: test name, value, unit, reference range and flag. Keep thousands separators, decimal points and the original capitalization of units. Do not clean, translate or expand anything.
2. Never guess, infer, calculate, convert or complete anything. If a field is not printed on the same row as the test, use null. Do not copy a unit or a reference range from another row.
3. A result row has a test name and a value. Do not output headings or section titles (for example HAEMATOLOGY or DIFFERENTIAL COUNT), notes, methods, signatures, contact details or tables that explain causes of abnormal results.
4. unit: the unit printed on that row, exactly as written. Otherwise null.
5. reference_range: the interval printed on that row, exactly as written (for example "13 - 17" or "< 2"). Otherwise null.
6. flag: a flag printed on that row (H, L, HIGH, LOW, NORMAL and similar), exactly as written. It can come before or after the value. Never work out a flag from the reference range. Otherwise null.
7. patient: id (registration, UHID or MRN number), name, sex, age_years, dob. collected_at, reported_at and lab_name only if printed. Copy dates exactly as written. Otherwise null.
8. If there are no results, return an empty results list.
9. Output only the JSON object, with no explanation.

Example input:
Ms. Jane Roe | Reg. no. : 5001
Age / Sex | : 41 YRS / F | Collected on : 03/02/2025
TEST | VALUE | UNIT | REFERENCE
Hemoglobin | 12.5 | g/dl | 12 - 15
DIFFERENTIAL COUNT
LYMPHOCYTE | L | 18 | % | 20 - 40
Glucose | 5.4

Example output:
{"patient": {"id": "5001", "name": "Ms. Jane Roe", "sex": "F", "age_years": 41, "dob": null}, "lab_name": null, "collected_at": "03/02/2025", "reported_at": null, "results": [{"test_name": "Hemoglobin", "value": "12.5", "unit": "g/dl", "reference_range": "12 - 15", "flag": null}, {"test_name": "LYMPHOCYTE", "value": "18", "unit": "%", "reference_range": "20 - 40", "flag": "L"}, {"test_name": "Glucose", "value": "5.4", "unit": null, "reference_range": null, "flag": null}]}"""

JSON_SPEC = """{
  "patient": {"id": string|null, "name": string|null, "sex": string|null, "age_years": number|null, "dob": string|null},
  "lab_name": string|null,
  "collected_at": string|null,
  "reported_at": string|null,
  "results": [{"test_name": string, "value": string, "unit": string|null, "reference_range": string|null, "flag": string|null}]
}"""


def _error_text(body):
    try:
        obj = json.loads(body)
        err = obj.get("error", obj) if isinstance(obj, dict) else obj
        if isinstance(err, dict):
            err = err.get("message") or json.dumps(err)
        return str(err)
    except ValueError:
        return body


CREDIT_RE = re.compile(r"credit|quota|billing|payment|insufficient|depleted|exhaust|out of funds", re.I)
MODEL_UNAVAILABLE = re.compile(r"not supported|not found|does not exist|unknown model|model_not_found|not available|no provider|invalid model", re.I)
AUTH_RE = re.compile(r"api key not valid|invalid api key|api_key_invalid|incorrect api key|invalid_api_key|unauthorized", re.I)
BLOCKING_KINDS = ("credits", "auth", "permission")

FRIENDLY = {
    "credits": "credits exhausted on the provider account",
    "auth": "the API key was rejected (invalid, revoked or mistyped)",
    "permission": "the API key has no permission to call the model (Hugging Face: enable 'Make calls to Inference Providers' on the token; Gemini: check the key's API restrictions)",
    "rate_limit": "rate limit reached",
    "server": "the provider is temporarily unavailable",
    "network": "the provider could not be reached",
}


def classify_http(code, detail):
    d = detail or ""
    if code == 402 or (code in (400, 401, 403, 429) and CREDIT_RE.search(d)):
        return "credits"
    if code == 401 or (code in (400, 403) and AUTH_RE.search(d)):
        return "auth"
    if code == 403:
        return "permission"
    if code == 429:
        return "rate_limit"
    if code in (400, 404, 422) and MODEL_UNAVAILABLE.search(d):
        return "model"
    if code >= 500:
        return "server"
    return "other"


class HttpError(RuntimeError):
    def __init__(self, code, body):
        self.detail = _error_text(body)[:400]
        super().__init__(f"HTTP {code}: {self.detail}")
        self.code = code
        self.kind = classify_http(code, self.detail)


def error_kind(e):
    kind = getattr(e, "kind", None)
    if kind:
        return kind
    return "network" if "connection failed" in str(e) else "other"


def describe_error(e):
    """One readable line: what went wrong in plain words, then the provider's own message."""
    kind = error_kind(e)
    return f"{FRIENDLY[kind]} - {e}" if kind in FRIENDLY else str(e)


# One entry per supported provider.
#   style        wire format: "openai" (/chat/completions), "gemini" (native generateContent), "ollama", "databricks"
#   key_vars     environment variables searched for the API key, first one that is set wins
#   model_vars   environment variables that override the model (<PREFIX>_MODEL first, then older names)
#   prefix       prefix for <PREFIX>_BASE_URL and <PREFIX>_FALLBACK_MODELS overrides
PROVIDERS = {
    "huggingface": {"label": "Hugging Face router", "style": "openai", "base_url": "https://router.huggingface.co/v1", "default_model": "Qwen/Qwen3-8B",
                    "key_vars": ("HF_TOKEN", "HF_API_KEY", "HUGGINGFACE_API_KEY"), "model_vars": ("HF_MODEL", "HUGGINGFACE_MODEL", "PRIMARY_MODEL"), "prefix": "HF"},
    "gemini": {"label": "Google Gemini", "style": "gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta", "default_model": "gemini-2.5-flash",
               "key_vars": ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "model_vars": ("GEMINI_MODEL", "FALLBACK_MODEL"), "prefix": "GEMINI"},
    "groq": {"label": "Groq", "style": "openai", "base_url": "https://api.groq.com/openai/v1", "default_model": "llama-3.3-70b-versatile",
             "key_vars": ("GROQ_API_KEY",), "model_vars": ("GROQ_MODEL", "SECONDARY_MODEL"), "prefix": "GROQ"},
    "openrouter": {"label": "OpenRouter", "style": "openai", "base_url": "https://openrouter.ai/api/v1", "default_model": "meta-llama/llama-3.3-70b-instruct",
                   "key_vars": ("OPENROUTER_API_KEY",), "model_vars": ("OPENROUTER_MODEL",), "prefix": "OPENROUTER"},
    "openai": {"label": "OpenAI", "style": "openai", "base_url": "https://api.openai.com/v1", "default_model": "gpt-4o-mini",
               "key_vars": ("OPENAI_API_KEY",), "model_vars": ("OPENAI_MODEL",), "prefix": "OPENAI"},
    "openai_compatible": {"label": "OpenAI-compatible endpoint", "style": "openai", "base_url": None, "default_model": None,
                          "key_vars": ("OPENAI_COMPATIBLE_API_KEY",), "model_vars": ("OPENAI_COMPATIBLE_MODEL",), "prefix": "OPENAI_COMPATIBLE"},
    "ollama": {"label": "Ollama (local)", "style": "ollama", "base_url": "http://localhost:11434", "default_model": None,
               "key_vars": (), "model_vars": ("OLLAMA_MODEL",), "prefix": "OLLAMA", "needs_key": False},
    "databricks": {"label": "Databricks serving endpoint", "style": "databricks", "base_url": None, "default_model": None,
                   "key_vars": ("DATABRICKS_TOKEN",), "model_vars": ("DATABRICKS_MODEL",), "prefix": "DATABRICKS"},
}
OPENAI_STYLE = tuple(k for k, v in PROVIDERS.items() if v["style"] == "openai")
DEFAULT_BASES = {k: v["base_url"] for k, v in PROVIDERS.items() if v["base_url"]}
LEGACY_KEY_VAR = "LLM_API_KEY"


def all_key_vars():
    """Every environment variable that can hold an API key, legacy one first."""
    out = [LEGACY_KEY_VAR]
    for spec in PROVIDERS.values():
        out += [v for v in spec["key_vars"] if v not in out]
    return out


def _fingerprint(key):
    k = (key or "").strip()
    if not k:
        return "none"
    if len(k) <= 10:
        return f"...{k[-2:]} ({len(k)} chars)"
    return f"{k[:3] if k.startswith('hf_') else k[:2]}...{k[-4:]} ({len(k)} chars)"


def strip_think(text):
    if not text:
        return text
    s = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    if "<think>" in s.lower():
        s = re.split(r"<think>", s, flags=re.I)[0]
    return s.strip()


class LlmClient:
    def __init__(self, provider=None, model=None, api_key=None, base_url=None, timeout=120, mode="primary", fallback_models=None, key_source=None, key_var=None, temperature=0, max_tokens=4096):
        self.provider = (provider or "").lower() or None
        self.model = model
        self.fallback_models = [m for m in (fallback_models or []) if m and m != model]
        self.active_model = None
        self._bad_models = {}
        # A trailing newline or space pasted with the key makes the provider answer 401.
        self.api_key = api_key.strip().strip("\"'") if isinstance(api_key, str) else api_key
        self.key_source = key_source or ("ui" if self.api_key else "not set")
        self.key_var = key_var
        self.base_url = base_url
        self.timeout = timeout
        self.temperature = 0 if temperature is None else temperature
        self.max_tokens = max_tokens or 4096
        self.mode = mode if mode in MODES else "primary"
        self._no_response_format = False
        self.blocked_reason = None
        self.stats = {"calls": 0, "ok": 0, "failed": 0, "seconds": 0.0, "last_error": None, "last_kind": None, "last_model": None}

    KEY_VARS = tuple(all_key_vars())

    @classmethod
    def env_key_candidates(cls):
        """Every API-key variable that is set, with where it came from. Never returns the key itself."""
        out = []
        for var in cls.KEY_VARS:
            val = (os.environ.get(var) or "").strip()
            if val:
                out.append({"variable": var, "source": env_source(var), "fingerprint": _fingerprint(val), "_value": val})
        return out

    @classmethod
    def from_env(cls, defaults=None):
        d = defaults or {}
        env = os.environ
        if d.get("providers") or env.get("LLM_PROVIDERS"):
            from .llm_router import build_llm  # multi-provider config: hand over to the router
            return build_llm(d)
        base = env.get("LLM_BASE_URL") or d.get("base_url")
        provider = env.get("LLM_PROVIDER") or d.get("provider")
        if not provider and base:
            provider = "huggingface" if "huggingface.co" in base else "openai_compatible"
        model = env.get("LLM_MODEL_NAME") or env.get("LLM_MODEL") or d.get("model")
        cands = cls.env_key_candidates()
        key = cands[0]["_value"] if cands else None
        key_var = cands[0]["variable"] if cands else None
        key_source = cands[0]["source"] if cands else "not set"
        mode = env.get("LLM_MODE") or d.get("mode") or "primary"
        fb = env.get("LLM_FALLBACK_MODELS")
        fallbacks = [x.strip() for x in fb.split(",")] if fb else list(d.get("fallback_models") or [])
        return cls(provider, model, key, base, mode=mode, fallback_models=fallbacks, key_source=key_source, key_var=key_var,
                   temperature=d.get("temperature", 0), max_tokens=d.get("max_tokens", 4096))

    @property
    def enabled(self):
        if not (self.provider and self.provider != "none" and self.model and self.mode != "off"):
            return False
        if self.provider not in PROVIDERS:
            return False
        return PROVIDERS[self.provider].get("needs_key", True) is False or bool(self.api_key)

    def status(self):
        if not self.provider or self.provider == "none":
            return "off: no provider set"
        if self.provider not in PROVIDERS:
            return f"off: unknown provider {self.provider} (supported: {', '.join(PROVIDERS)})"
        if not self.model:
            return "off: no model set"
        if self.mode == "off":
            return "off: mode is off"
        if PROVIDERS[self.provider].get("needs_key", True) and not self.api_key:
            return "off: no API key found (set " + " or ".join(PROVIDERS[self.provider]["key_vars"] or (LEGACY_KEY_VAR,)) + ")"
        fb = f", {len(self.fallback_models)} fallback model(s)" if self.fallback_models else ""
        return f"ready: {self.provider}, model {self.model}{fb}, mode {self.mode}"

    def key_fingerprint(self):
        return _fingerprint(self.api_key)

    def key_origin(self):
        """Human text for where the key in use was read from."""
        if not self.api_key:
            return "no key"
        names = {"ui": "typed in the app sidebar", "dotenv": "the .env file", "environment": "an environment variable / Databricks App setting"}
        base = names.get(self.key_source, self.key_source)
        return f"{base} ({self.key_var})" if self.key_var and self.key_source != "ui" else base

    def whoami(self):
        """Hugging Face only: which account and token the key really belongs to. Costs no credits."""
        if self.provider != "huggingface" or not self.api_key:
            return None
        url = getattr(self, "whoami_url", None) or "https://huggingface.co/api/whoami-v2"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return {"valid": False, "error": f"HTTP {e.code}: " + _error_text(e.read().decode("utf-8", "replace"))[:200]}
        except Exception as e:
            return {"valid": None, "error": f"could not check: {str(e)[:200]}"}
        tok = (data.get("auth") or {}).get("accessToken") or {}
        return {
            "valid": True, "account": data.get("name"), "account_type": data.get("type"),
            "pro": data.get("isPro"), "can_pay": data.get("canPay"),
            "token_name": tok.get("displayName"), "token_role": tok.get("role"),
            "orgs": [o.get("name") for o in (data.get("orgs") or []) if isinstance(o, dict)],
        }

    def _post(self, url, body, headers):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers}, method="POST")
        last = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                last = HttpError(e.code, e.read().decode("utf-8", "replace"))
                if e.code not in (429, 502, 503, 504):
                    raise last
            except urllib.error.URLError as e:
                last = RuntimeError(f"connection failed: {e.reason}")
            time.sleep(1.5 * (attempt + 1))
        raise last

    def _chat(self, base, body, auth):
        try:
            return self._post(base + "/chat/completions", body, auth)
        except HttpError as e:
            mentions_format = "response_format" in e.detail.lower() or "json_object" in e.detail.lower() or "json mode" in e.detail.lower()
            if e.code in (400, 422) and "response_format" in body and (mentions_format or not MODEL_UNAVAILABLE.search(e.detail)):
                body = {k: v for k, v in body.items() if k != "response_format"}
                self._no_response_format = True
                return self._post(base + "/chat/completions", body, auth)
            raise

    def candidate_models(self):
        order = [self.active_model] if self.active_model else []
        order += [m for m in [self.model] + self.fallback_models if m not in order]
        return [m for m in order if m and m not in self._bad_models]

    def complete(self, system, user):
        """Calls the model and records what happened, so the app can show whether the LLM was really used."""
        t0 = time.time()
        self.stats["calls"] += 1
        try:
            out = self._complete(system, user)
        except Exception as e:
            self.stats["failed"] += 1
            self.stats["last_kind"] = error_kind(e)
            self.stats["last_error"] = describe_error(e)[:500]
            if self.stats["last_kind"] in BLOCKING_KINDS:
                self.blocked_reason = self.stats["last_error"]
            raise
        finally:
            self.stats["seconds"] += time.time() - t0
        self.stats["ok"] += 1
        self.stats["last_model"] = self.active_model
        return out

    def _across_models(self, call):
        """Runs call(model) for each candidate model until one works. Models the provider says it does not have are remembered and skipped."""
        tried = []
        for model in self.candidate_models():
            try:
                out = call(model)
            except HttpError as e:
                if e.code in (400, 404, 422) and MODEL_UNAVAILABLE.search(e.detail):
                    self._bad_models[model] = e.detail
                    tried.append(f"{model}: {e.detail[:120]}")
                    continue
                raise
            self.active_model = model
            return out
        if not tried and self._bad_models:
            tried = [f"{m}: {d[:120]}" for m, d in self._bad_models.items()]
        raise RuntimeError("no usable model. " + " | ".join(tried) if tried else "no model configured")

    def _gemini_call(self, base, model, system, user):
        """Native Gemini generateContent call. The key travels in a header, never in the URL."""
        gen = {"temperature": self.temperature, "maxOutputTokens": self.max_tokens, "responseMimeType": "application/json"}
        low = model.lower()
        if "2.5" in low and "flash" in low:
            gen["thinkingConfig"] = {"thinkingBudget": 0}  # thinking tokens would otherwise eat the output budget and cut the JSON off
        elif "2.5" in low:
            gen["maxOutputTokens"] = max(self.max_tokens, 8192)
        body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": gen}
        url = f"{base}/models/{model}:generateContent"
        headers = {"x-goog-api-key": self.api_key or ""}
        try:
            out = self._post(url, body, headers)
        except HttpError as e:
            if e.code in (400, 422) and "thinking" in e.detail.lower() and "thinkingConfig" in gen:
                del gen["thinkingConfig"]
                out = self._post(url, body, headers)
            else:
                raise
        cands = out.get("candidates") or []
        parts = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
        if not text.strip():
            why = (out.get("promptFeedback") or {}).get("blockReason") or (cands[0].get("finishReason") if cands else None) or "no candidates"
            raise RuntimeError(f"Gemini returned no text ({why})")
        return strip_think(text)

    def _complete(self, system, user):
        spec = PROVIDERS.get(self.provider)
        if spec is None:
            raise ValueError(f"unknown LLM provider {self.provider}")
        style = spec["style"]
        auth = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        if style == "ollama":
            base = (self.base_url or DEFAULT_BASES["ollama"]).rstrip("/")
            msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            out = self._post(base + "/api/chat", {"model": self.model, "messages": msgs, "stream": False, "format": "json", "options": {"temperature": self.temperature}}, {})
            self.active_model = self.model
            return strip_think(out["message"]["content"])
        if style == "databricks":
            base = (self.base_url or os.environ.get("DATABRICKS_HOST", "")).rstrip("/")
            if not base.startswith("http"):
                base = "https://" + base
            msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            out = self._post(f"{base}/serving-endpoints/{self.model}/invocations", {"messages": msgs, "temperature": self.temperature, "max_tokens": self.max_tokens}, auth)
            self.active_model = self.model
            return strip_think(out["choices"][0]["message"]["content"])
        if style == "gemini":
            base = (self.base_url or DEFAULT_BASES["gemini"]).rstrip("/")
            return self._across_models(lambda model: self._gemini_call(base, model, system, user))
        if style == "openai":
            base = (self.base_url or spec["base_url"] or DEFAULT_BASES["openai"]).rstrip("/")

            def call(model):
                suffix = " /no_think" if "qwen3" in model.lower() else ""
                msgs = [{"role": "system", "content": system}, {"role": "user", "content": user + suffix}]
                body = {"model": model, "messages": msgs, "temperature": self.temperature, "max_tokens": self.max_tokens}
                if not self._no_response_format:
                    body["response_format"] = {"type": "json_object"}
                out = self._chat(base, body, auth)
                return strip_think(out["choices"][0]["message"]["content"])
            return self._across_models(call)
        raise ValueError(f"unknown LLM provider {self.provider}")

    def list_models(self):
        base = (self.base_url or DEFAULT_BASES.get(self.provider, "")).rstrip("/")
        gemini = (PROVIDERS.get(self.provider) or {}).get("style") == "gemini"
        if gemini:
            headers = {"x-goog-api-key": self.api_key or ""}
        else:
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        req = urllib.request.Request(base + ("/models?pageSize=200" if gemini else "/models"), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise HttpError(e.code, e.read().decode("utf-8", "replace"))
        out = []
        if gemini:
            for m in data.get("models", []):
                if "generateContent" in (m.get("supportedGenerationMethods") or ["generateContent"]):
                    out.append({"id": str(m.get("name", "")).replace("models/", "", 1), "providers": "google"})
            return out
        for m in data.get("data", []):
            provs = m.get("providers") or []
            names = [p.get("provider") for p in provs if isinstance(p, dict) and p.get("status", "live") == "live"]
            out.append({"id": m.get("id"), "providers": ", ".join(n for n in names if n)})
        return out

    def ping(self):
        try:
            out = self.complete("Reply with only this JSON: {\"ok\": true}", "Reply now.")
            ok = isinstance(parse_json(out), dict)
            return ok, (f"connected, model {self.active_model}" if ok else f"the model answered but not with JSON: {str(out)[:120]}")
        except Exception as e:
            return False, str(e)[:500]

    def extract(self, text):
        merged = None
        for chunk in chunks(text):
            user = f"Report text:\n\"\"\"\n{chunk}\n\"\"\"\n\nReturn JSON in this shape:\n{JSON_SPEC}"
            obj = check_output(parse_json(self.complete(SYSTEM_PROMPT, user)))
            merged = join_outputs(merged, obj)
        return merged


def chunks(text, limit=CHUNK_CHARS):
    pages = [p for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    out, cur = [], ""
    for p in pages:
        if len(p) > limit:
            lines, buf = p.splitlines(), ""
            for ln in lines:
                if buf and len(buf) + len(ln) + 1 > limit:
                    out.append(buf)
                    buf = ""
                buf += ln + "\n"
            if buf:
                out.append(buf)
            continue
        if cur and len(cur) + len(p) + 2 > limit:
            out.append(cur)
            cur = ""
        cur += p + "\n\n"
    if cur.strip():
        out.append(cur)
    return out or [text or ""]


def parse_json(raw):
    if not raw:
        return None
    s = strip_think(raw).strip()
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
    try:
        return json.loads(s)
    except ValueError:
        m = re.search(r"\{.*\}", s, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                return None
    return None


def check_output(obj):
    if not isinstance(obj, dict):
        return None
    p = obj.get("patient")
    out = {"patient": p if isinstance(p, dict) else {}, "lab_name": obj.get("lab_name"), "collected_at": obj.get("collected_at"), "reported_at": obj.get("reported_at"), "results": []}
    for r in obj.get("results") or []:
        if isinstance(r, dict) and isinstance(r.get("test_name"), str) and r.get("value") is not None and not isinstance(r.get("value"), (dict, list)):
            out["results"].append(r)
    return out


def join_outputs(a, b):
    if b is None:
        return a
    if a is None:
        return b
    for k, v in b["patient"].items():
        if a["patient"].get(k) in (None, "") and v not in (None, ""):
            a["patient"][k] = v
    for k in ("lab_name", "collected_at", "reported_at"):
        if not a.get(k) and b.get(k):
            a[k] = b[k]
    a["results"] += b["results"]
    return a


def _squash(s):
    return re.sub(r"[\s|]+", "", str(s).lower())


def in_text(value, text_sq):
    return value is not None and str(value).strip() != "" and _squash(value) in text_sq


def _value_forms(value):
    forms = []
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        forms += [str(value), f"{value:g}"]
    else:
        forms.append(str(value).strip())
    return [f.replace(",", "") for f in forms if f]


def _looks_like_row(line):
    cells = [c.strip() for c in line.split("|")]
    return len(cells) >= 2 and bool(re.match(r"^[A-Za-z]", cells[0])) and bool(re.match(r"^[<>]?\s*\d", cells[1]))


def _ref_window(lines, i):
    out = [lines[i]]
    for ln in lines[i + 1:i + 6]:
        if _looks_like_row(ln):
            break
        out.append(ln)
    return " | ".join(out)


def row_window(name, value, text, with_ref=False):
    name_sq = _squash(name)
    forms = _value_forms(value)
    if not name_sq or not forms:
        return None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if name_sq in _squash(line):
            window = line + " | " + (lines[i + 1] if i + 1 < len(lines) else "")
            tokens = [t.replace(",", "") for t in re.split(r"[\s|]+", window)]
            for f in forms:
                if f in tokens:
                    k = i
                    if f not in [t.replace(",", "") for t in re.split(r"[\s|]+", line)] and i + 1 < len(lines):
                        k = i + 1
                    return (window, f, _ref_window(lines, k)) if with_ref else (window, f)
    return None


def row_in_text(name, value, text):
    return row_window(name, value, text) is not None


def _in_window(item, window):
    if item is None or str(item).strip() == "":
        return False
    item = str(item).strip()
    tokens = [t for t in re.split(r"[\s|]+", window) if t]
    if item in tokens:
        return True
    return (" " in item or len(item) > 3) and _squash(item) in _squash(window)


def verify(extracted, text, conf):
    if not isinstance(extracted, dict):
        return None
    sq = _squash(text)
    p = extracted.get("patient") or {}
    name_raw = p.get("name") if in_text(p.get("name"), sq) else None
    title, name = split_title(name_raw)
    age = p.get("age_years")
    patient = {
        "id": str(p["id"]) if in_text(p.get("id"), sq) else None,
        "name": name,
        "name_raw": name_raw,
        "sex": norm_sex(str(p.get("sex"))) if p.get("sex") and in_text(p.get("sex"), sq) else None,
        "age_years": int(age) if isinstance(age, (int, float)) and not isinstance(age, bool) and in_text(int(age), sq) else None,
        "dob": to_iso(p.get("dob")) if in_text(p.get("dob"), sq) else None,
    }
    rows, dropped = [], 0
    for r in extracted.get("results") or []:
        if not isinstance(r, dict):
            continue
        if NOT_TEST_RE.match(str(r.get("test_name") or "").strip()):
            dropped += 1
            continue
        hit = row_window(r.get("test_name"), r.get("value"), text, with_ref=True)
        if hit is None:
            dropped += 1
            continue
        window, val, ref_win = hit
        if isinstance(r.get("value"), str):
            val = r["value"].strip()
        unit = r.get("unit") if _in_window(r.get("unit"), window) and str(r.get("unit")).strip().lower() not in NOT_UNITS else None
        ref = r.get("reference_range") if _in_window(r.get("reference_range"), ref_win) else None
        flag = r.get("flag") if _in_window(r.get("flag"), window) else None
        row = make_row(str(r["test_name"]).strip(), val, unit, ref, flag, min(conf if conf is not None else 0.85, 0.85))
        row["extraction_method"] = "llm_verified"
        rows.append(row)
    return {
        "patient": patient,
        "lab_name": extracted.get("lab_name") if in_text(extracted.get("lab_name"), sq) else None,
        "collected_at": to_iso(extracted.get("collected_at")) if in_text(extracted.get("collected_at"), sq) else None,
        "reported_at": to_iso(extracted.get("reported_at")) if in_text(extracted.get("reported_at"), sq) else None,
        "rows": rows,
        "dropped": dropped,
    }


def _key(row):
    return (re.sub(r"[^a-z0-9]", "", str(row["raw_test_name"]).lower()), re.sub(r"[,\s]", "", str(row["raw_value"])))


def _fill_meta(target, source):
    changed = False
    for k, v in source["patient"].items():
        if target["patient"].get(k) in (None, "") and v not in (None, ""):
            target["patient"][k] = v
            changed = True
    for k in ("lab_name", "collected_at", "reported_at"):
        if not target.get(k) and source.get(k):
            target[k] = source[k]
            changed = True
    return changed


def merge(rule_reports, llm_report, mode):
    note = ""
    if llm_report and llm_report.get("dropped"):
        note = f" ({llm_report['dropped']} unverifiable result(s) dropped)"
    if not llm_report or (not llm_report["rows"] and not any(llm_report["patient"].values())):
        return rule_reports, "rules (the LLM returned nothing that could be verified)" if llm_report is not None else "rules"
    if mode == "primary":
        if llm_report["rows"]:
            rep = {k: v for k, v in llm_report.items() if k != "dropped"}
            rep["patient"] = dict(rep["patient"])
            if rule_reports:
                _fill_meta(rep, rule_reports[0])
            return [rep], "llm_primary" + note
        out = [dict(r) for r in rule_reports]
        return out, "rules (the LLM found no verifiable results)" + note
    if not rule_reports:
        if llm_report["rows"]:
            return [{k: v for k, v in llm_report.items() if k != "dropped"}], "llm_fallback" + note
        return rule_reports, "rules"
    out = [dict(r) for r in rule_reports]
    if mode != "assist":
        filled = any(_fill_meta(rep, llm_report) for rep in out)
        return out, ("rules+llm_metadata" if filled else "rules") + note
    first = out[0]
    have = {_key(r) for rep in out for r in rep["rows"]}
    added = False
    for r in llm_report["rows"]:
        if _key(r) not in have:
            first["rows"] = list(first["rows"]) + [r]
            added = True
    added = _fill_meta(first, llm_report) or added
    return out, ("rules+llm_assist" if added else "rules") + note
