"""
LLM Configuration — Multi-provider with automatic API key rotation.

This is the SINGLE source of truth for all LLM configuration in the project.

Per-node provider selection
───────────────────────────
Each *_MODEL env var accepts an optional provider prefix:

    FAILURE_PARSER_MODEL = groq/llama-3.1-8b-instant
    FILE_FETCHER_MODEL   = ollama/llama3.2:1b
    CODE_FIXER_MODEL     = openrouter/anthropic/claude-opus-4

Supported prefixes (determines provider and API key used):
    groq/          — Groq cloud          (GROQ_API_KEY)
    openai/        — OpenAI              (OPENAI_API_KEY)
    anthropic/     — Anthropic           (ANTHROPIC_API_KEY)
    google/        — Google Gemini       (GEMINI_API_KEY)
    openrouter/    — OpenRouter          (OPENROUTER_API_KEY)
    ollama/        — Local Ollama server (no API key, uses OLLAMA_BASE_URL)

No prefix: auto-detected from model name pattern, defaults to groq.

Key rotation:
    Each API key env var accepts comma-separated keys.
    On rate-limit, the next key is tried automatically.
    Error raised only when ALL keys are exhausted.
"""
import os
import re
from dataclasses import dataclass
from typing import Iterator, List, Optional

from rich.console import Console

console = Console()


# ── Config dataclass ──────────────────────────────────────────────────────────

@dataclass
class LLMConfig:
    """
    Plain configuration object passed to AIWrapper / create_llm().
    Provider is auto-detected from the model name if not supplied.
    """
    provider: str = "groq"
    model: str = ""
    temperature: float = 0.0
    api_key: Optional[str] = None
    streaming: bool = False

    def __post_init__(self):
        if not self.model:
            self.model = os.getenv("DEFAULT_MODEL", "").strip()
        if not self.model:
            raise EnvironmentError("No LLM model name specified and no DEFAULT_MODEL set in .env")


def get_model_from_env(key: str) -> str:
    """Get the model name from the environment.
    Falls back to DEFAULT_MODEL if the key is not set or empty.
    Raises EnvironmentError if neither is defined.
    """
    model = os.getenv(key, "").strip()
    if not model:
        model = os.getenv("DEFAULT_MODEL", "").strip()
    if not model:
        raise EnvironmentError(
            f"Model variable '{key}' is not defined in the environment (or .env file) "
            f"and no fallback 'DEFAULT_MODEL' is specified."
        )
    return model


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_env(key: str) -> str:
    """Read an env var; raise a clear, actionable error if it is missing."""
    value = os.getenv(key, "").strip()
    if not value:
        raise EnvironmentError(
            f"\n\n  Error: {key} is not set in your .env file.\n"
            f"      Open .env and add:  {key} = <value>\n"
        )
    return value


def _parse_keys(raw: str) -> List[str]:
    """Split a comma-separated string of API keys, dropping empty entries."""
    return [k.strip() for k in raw.split(",") if k.strip()]


# All recognized provider prefixes — order matters for prefix-strip logic
_KNOWN_PREFIXES = ("groq", "openai", "anthropic", "google", "openrouter", "ollama")


def _strip_provider_prefix(model: str) -> str:
    """Strip the provider prefix from a model name.

    'groq/llama-3.1-8b-instant'              →  'llama-3.1-8b-instant'
    'openrouter/meta-llama/llama-3.1-8b'     →  'meta-llama/llama-3.1-8b'
    'ollama/llama3.2:1b'                     →  'llama3.2:1b'
    'llama-3.1-8b-instant'                   →  'llama-3.1-8b-instant'  (unchanged)
    """
    for prefix in _KNOWN_PREFIXES:
        if model.startswith(f"{prefix}/"):
            return model[len(prefix) + 1:]
    return model


def _detect_provider(model: str) -> str:
    """Infer the provider from the model name or env flags.

    Priority:
      1. Explicit provider prefix (e.g. 'groq/...', 'ollama/...') — per-node
      2. LLM_PROVIDER env var — global override for all non-prefixed models
      3. Auto-detect from well-known model name patterns
      4. Default → groq
    """
    # 1. Explicit prefix — always wins, enables per-node provider control
    for prefix in _KNOWN_PREFIXES:
        if model.startswith(f"{prefix}/"):
            return prefix

    # 2. LLM_PROVIDER explicit global override
    forced = os.getenv("LLM_PROVIDER", "").lower().strip()
    if forced:
        return forced

    # 3. Auto-detect from well-known model name patterns
    if model.startswith(("gpt-", "o1-", "o3-")):
        return "openai"
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith("gemini-"):
        return "google"

    # 4. Default
    return "groq"


def _is_rate_limit_error(exc: Exception) -> bool:
    """Return True if the exception looks like a quota / rate-limit error."""
    msg = str(exc).lower()
    if "413" in msg or "too large" in msg:
        return False
    return any(token in msg for token in [
        "rate_limit", "rate limit", "resource_exhausted",
        "quota_exceeded", "quota exceeded",
        "too many requests", "tokens per", "requests per",
        "429", "402", "insufficient credits", "payment required", "requires more credits",
        "401", "invalid api key", "invalid_api_key", "unauthorized",
    ])


def _is_tool_call_failed_error(exc: Exception) -> bool:
    """Return True if Groq rejected a tool-call generation (400 tool_use_failed).

    This happens when a key starts generating a response in XML / Hermes format
    instead of native JSON function calls and then gets rejected mid-stream.
    Rotating to a fresh key gives a clean context without the corrupt generation.
    """
    msg = str(exc)
    return "tool_use_failed" in msg or (
        "400" in msg and "tool call validation failed" in msg
    )


def _is_openrouter_loop_error(exc: Exception) -> bool:
    """Return True if OpenRouter flagged the output as looping/repetitive content.

    OpenRouter's safety filter rejects requests when it detects repetitive or
    looping content in the prompt (e.g. very long tracebacks, repeated code).
    The fix is to append '[ignoring loop detection]' to the prompt and retry.
    """
    msg = str(exc).lower()
    return "looping content" in msg or "loop detection" in msg


def _append_loop_bypass(messages: list) -> list:
    """Return a copy of messages with the OpenRouter loop-bypass tag appended
    to the content of the last HumanMessage."""
    from langchain_core.messages import HumanMessage
    patched = list(messages)
    for i in range(len(patched) - 1, -1, -1):
        if isinstance(patched[i], HumanMessage):
            original = patched[i].content
            if "[ignoring loop detection]" not in original:
                patched[i] = HumanMessage(
                    content=original + "\n\n[ignoring loop detection]"
                )
            break
    return patched


def _make_single_llm(provider: str, model: str, api_key: str,
                     temperature: float, **kwargs):
    """
    Instantiate a single LangChain chat model.
    'model' must already have its provider prefix stripped (done by get_llm).
    """
    if provider in ("openai", "anthropic", "google", "groq", "openrouter"):
        kwargs.setdefault("timeout", 120.0)

    if provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError("Run: pip install langchain-openai")
        return ChatOpenAI(model=model, temperature=temperature,
                          api_key=api_key, **kwargs)

    elif provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ImportError("Run: pip install langchain-anthropic")
        return ChatAnthropic(model=model, temperature=temperature,
                             api_key=api_key, **kwargs)

    elif provider == "google":
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError:
            raise ImportError("Run: pip install langchain-google-genai")
        kwargs.setdefault("max_retries", 1)
        return ChatGoogleGenerativeAI(model=model, temperature=temperature,
                                      google_api_key=api_key, **kwargs)

    elif provider == "groq":
        try:
            from langchain_groq import ChatGroq
        except ImportError:
            raise ImportError("Run: pip install langchain-groq")
        return ChatGroq(model=model, temperature=temperature,
                        api_key=api_key, **kwargs)

    elif provider == "openrouter":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError("Run: pip install langchain-openai")
        return ChatOpenAI(
            model=model,
            temperature=temperature,
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            **kwargs,
        )

    elif provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError:
            raise ImportError("Run: pip install langchain-ollama")
        # base_url is passed in via kwargs from get_llm()
        return ChatOllama(model=model, temperature=temperature, **kwargs)

    else:
        raise ValueError(
            f"Unknown provider {provider!r}. "
            f"Valid prefixes: {', '.join(_KNOWN_PREFIXES)}"
        )


# ── Key-rotating LLM wrapper ──────────────────────────────────────────────────

class RotatingLLM:
    """
    Wraps N LLM instances (one per API key).
    On rate-limit / quota errors, automatically rotates to the next key.
    Only raises RuntimeError when ALL keys have been tried and failed.

    Supports both .invoke() and .stream() — the same rotation logic applies
    to streaming (if a key fails before / during streaming, the next key
    restarts the full stream from the beginning).
    """

    def __init__(self, llms: list, labels: List[str], model: str, shared_idx: list = None, fallback_llm = None) -> None:
        self._llms   = llms
        self._labels = labels        # e.g. ["key 1/3", "key 2/3", "key 3/3"]
        self._model  = model
        self._shared_idx = shared_idx if shared_idx is not None else [0]
        self.fallback_llm = fallback_llm

    @property
    def _idx(self):
        return self._shared_idx[0]

    @_idx.setter
    def _idx(self, value):
        self._shared_idx[0] = value

    def bind_tools(self, tools, **kwargs):
        bound_llms = [llm.bind_tools(tools, **kwargs) for llm in self._llms]
        bound_fallback = self.fallback_llm.bind_tools(tools, **kwargs) if self.fallback_llm else None
        return RotatingLLM(bound_llms, self._labels, self._model, self._shared_idx, bound_fallback)

    def bind(self, **kwargs):
        bound_llms = [llm.bind(**kwargs) for llm in self._llms]
        bound_fallback = self.fallback_llm.bind(**kwargs) if self.fallback_llm else None
        return RotatingLLM(bound_llms, self._labels, self._model, self._shared_idx, bound_fallback)

    # ------------------------------------------------------------------
    def invoke(self, messages, **kwargs):
        errors = []
        n = len(self._llms)

        for attempt in range(n):
            idx = (self._idx + attempt) % n
            try:
                result = self._llms[idx].invoke(messages, **kwargs)
                self._idx = idx
                
                try:
                    from common_utils.token_tracker import token_tracker, extract_tokens
                    in_t, out_t = extract_tokens(result, messages)
                    token_tracker.record_usage(self._model, in_t, out_t)
                except Exception as tracker_exc:
                    console.print(f"[dim yellow]Token tracking error: {tracker_exc}[/dim yellow]")
                
                return result
            except Exception as exc:
                if _is_rate_limit_error(exc):
                    console.print(
                        f"[yellow]{self._model} {self._labels[idx]} "
                        f"rate-limited — trying next key…[/yellow]"
                    )
                    errors.append(f"{self._labels[idx]}: {str(exc)[:100]}")
                    continue
                if _is_tool_call_failed_error(exc):
                    console.print(
                        f"[yellow]{self._model} {self._labels[idx]} "
                        f"tool-call format error — rotating to next key…[/yellow]"
                    )
                    errors.append(f"{self._labels[idx]}: tool_use_failed")
                    continue
                if _is_openrouter_loop_error(exc):
                    console.print(
                        f"[yellow]{self._model} {self._labels[idx]} "
                        f"OpenRouter loop-detection triggered — retrying with bypass tag…[/yellow]"
                    )
                    try:
                        patched = _append_loop_bypass(list(messages))
                        result = self._llms[idx].invoke(patched, **kwargs)
                        self._idx = idx
                        
                        try:
                            from common_utils.token_tracker import token_tracker, extract_tokens
                            in_t, out_t = extract_tokens(result, patched)
                            token_tracker.record_usage(self._model, in_t, out_t)
                        except Exception as tracker_exc:
                            console.print(f"[dim yellow]Token tracking error: {tracker_exc}[/dim yellow]")
                            
                        return result
                    except Exception as inner_exc:
                        errors.append(f"{self._labels[idx]}: loop-bypass failed: {str(inner_exc)[:100]}")
                        continue
                
                if self.fallback_llm:
                    console.print(
                        f"[bold yellow]Model '{self._model}' failed with non-rotatable error: {exc}. "
                        f"Falling back to model '{self.fallback_llm._model}'...[/bold yellow]"
                    )
                    return self.fallback_llm.invoke(messages, **kwargs)
                raise
 
        if self.fallback_llm:
            console.print(
                f"[bold yellow]All keys for '{self._model}' failed. "
                f"Falling back to model '{self.fallback_llm._model}'...[/bold yellow]"
            )
            return self.fallback_llm.invoke(messages, **kwargs)

        raise RuntimeError(self._exhausted_msg(errors))

    # ------------------------------------------------------------------
    def stream(self, messages, **kwargs):
        errors = []
        n = len(self._llms)

        for attempt in range(n):
            idx = (self._idx + attempt) % n
            try:
                accumulated_content = []
                last_chunk = None
                
                for chunk in self._llms[idx].stream(messages, **kwargs):
                    last_chunk = chunk
                    if hasattr(chunk, 'content'):
                        accumulated_content.append(chunk.content)
                    else:
                        accumulated_content.append(str(chunk))
                    yield chunk
                
                self._idx = idx
            except Exception as exc:
                if _is_rate_limit_error(exc):
                    console.print(
                        f"\n[yellow]{self._model} {self._labels[idx]} "
                        f"rate-limited — trying next key…[/yellow]"
                    )
                    errors.append(f"{self._labels[idx]}: {str(exc)[:100]}")
                    continue
                if _is_tool_call_failed_error(exc):
                    console.print(
                        f"\n[yellow]{self._model} {self._labels[idx]} "
                        f"tool-call format error — rotating to next key…[/yellow]"
                    )
                    errors.append(f"{self._labels[idx]}: tool_use_failed")
                    continue
                if _is_openrouter_loop_error(exc):
                    console.print(
                        f"\n[yellow]{self._model} {self._labels[idx]} "
                        f"OpenRouter loop-detection triggered — retrying stream with bypass tag…[/yellow]"
                    )
                    try:
                        patched = _append_loop_bypass(list(messages))
                        accumulated_content = []
                        last_chunk = None
                        for chunk in self._llms[idx].stream(patched, **kwargs):
                            last_chunk = chunk
                            if hasattr(chunk, 'content'):
                                accumulated_content.append(chunk.content)
                            else:
                                accumulated_content.append(str(chunk))
                            yield chunk
                        self._idx = idx
                        
                        try:
                            from common_utils.token_tracker import token_tracker, extract_tokens
                            full_content = ''.join(accumulated_content)
                            in_t, out_t = extract_tokens(last_chunk or full_content, patched)
                            token_tracker.record_usage(self._model, in_t, out_t)
                        except Exception as tracker_exc:
                            pass
                            
                        return
                    except Exception as inner_exc:
                        errors.append(f"{self._labels[idx]}: loop-bypass stream failed: {str(inner_exc)[:100]}")
                        continue
                
                if self.fallback_llm:
                    console.print(
                        f"\n[bold yellow]Model '{self._model}' failed with non-rotatable error: {exc}. "
                        f"Falling back stream to model '{self.fallback_llm._model}'...[/bold yellow]"
                    )
                    yield from self.fallback_llm.stream(messages, **kwargs)
                    return
                raise
 
        if self.fallback_llm:
            console.print(
                f"\n[bold yellow]All keys for '{self._model}' failed. "
                f"Falling back stream to model '{self.fallback_llm._model}'...[/bold yellow]"
            )
            yield from self.fallback_llm.stream(messages, **kwargs)
            return

        raise RuntimeError(self._exhausted_msg(errors))

    def _exhausted_msg(self, errors: List[str]) -> str:
        n = len(self._llms)
        lines = "\n".join(f"    {e}" for e in errors)
        return (
            f"\n\n  Error: All {n} API key(s) for '{self._model}' are exhausted "
            f"(rate-limited / quota exceeded).\n"
            f"  Add more keys to your .env (comma-separated) or wait for quota reset.\n"
            f"  Errors:\n{lines}\n"
        )

    # ------------------------------------------------------------------
    @property
    def key_count(self) -> int:
        return len(self._llms)


_GLOBAL_KEY_INDICES = {}

# ── Public factory ────────────────────────────────────────────────────────────

def get_llm(model: str, temperature: float = 0, **kwargs) -> RotatingLLM:
    """
    Build a RotatingLLM for the given model string.

    'model' may include an optional provider prefix, e.g.:
        'groq/llama-3.1-8b-instant'
        'ollama/llama3.2:1b'
        'openrouter/meta-llama/llama-3.1-8b-instruct'
        'llama-3.3-70b-versatile'     (auto-detected as groq)

    Reads the API key env var for the detected provider.
    Multiple comma-separated keys are all loaded and rotated on rate-limit.
    """
    is_fallback = kwargs.pop("is_fallback", False)

    fallback_llm = None
    fallback_model = os.getenv("FALLBACK_MODEL", "").strip()
    if not fallback_model and (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        fallback_model = "google/gemini-2.5-flash"

    if not is_fallback and fallback_model and fallback_model != model:
        try:
            # We copy kwargs to avoid sharing state between primary and fallback
            fallback_kwargs = dict(kwargs)
            fallback_llm = get_llm(fallback_model, temperature=temperature, is_fallback=True, **fallback_kwargs)
        except Exception as fallback_exc:
            console.print(f"[dim yellow]Failed to load fallback model '{fallback_model}': {fallback_exc}[/dim yellow]")

    provider   = _detect_provider(model)
    bare_model = _strip_provider_prefix(model)   # strip 'groq/', 'ollama/', etc.

    # ── OpenRouter default max_tokens to prevent credit limits ─────────────────
    if provider == "openrouter" and "max_tokens" not in kwargs:
        kwargs["max_tokens"] = 1024

    # ── Ollama: no API key needed ──────────────────────────────────────────────
    if provider == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        console.print(f"[dim]Ollama  [{bare_model}]  {base_url}[/dim]")
        llm = _make_single_llm("ollama", bare_model, "", temperature,
                               base_url=base_url, **kwargs)
        return RotatingLLM([llm], ["local"], bare_model, fallback_llm=fallback_llm)

    # ── Cloud providers: load API key(s) ──────────────────────────────────────
    key_env_map = {
        "openai":     "OPENAI_API_KEY",
        "anthropic":  "ANTHROPIC_API_KEY",
        "google":     "GEMINI_API_KEY",
        "groq":       "GROQ_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }
    if provider not in key_env_map:
        raise ValueError(
            f"Unknown provider {provider!r}. "
            f"Valid prefixes: {', '.join(_KNOWN_PREFIXES)}"
        )

    key_env  = key_env_map[provider]
    raw_keys = _require_env(key_env)

    # Support alias: GOOGLE_API_KEY as fallback for GEMINI_API_KEY
    if provider == "google" and not raw_keys:
        raw_keys = _require_env("GOOGLE_API_KEY")

    keys = _parse_keys(raw_keys)
    if not keys:
        raise EnvironmentError(
            f"\n\n  Error: {key_env} is set but contains no valid keys.\n"
            f"      Use a comma-separated list: {key_env} = key1, key2\n"
        )

    llms   = [_make_single_llm(provider, bare_model, k, temperature, **kwargs)
              for k in keys]
    labels = [f"key {i+1}/{len(keys)}" for i in range(len(keys))]

    if len(keys) > 1:
        console.print(
            f"[dim]{bare_model}: {len(keys)} API keys loaded — "
            f"will rotate on rate-limit errors[/dim]"
        )

    if key_env not in _GLOBAL_KEY_INDICES:
        _GLOBAL_KEY_INDICES[key_env] = [0]
    shared_idx = _GLOBAL_KEY_INDICES[key_env]

    return RotatingLLM(llms, labels, bare_model, shared_idx, fallback_llm)




