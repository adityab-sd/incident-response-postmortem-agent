"""Tiered LLM client factory (`get_chat_model`) with retry, backoff, jitter, and provider fallback."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, Field, PrivateAttr

from incident_agent.config import AppConfig, ProviderEndpointConfig, get_config
from incident_agent.logging_utils import get_incident_context, get_logger, log_event
from incident_agent.schemas import ModelRole
from incident_agent.tracing.tracer import LLMTracer, estimate_token_count

logger = get_logger("models.factory")


class ProviderHTTPError(RuntimeError):
    """Error representing an HTTP status failure from an LLM provider (e.g., 429, 503)."""

    def __init__(self, provider: str, status_code: int, message: str) -> None:
        super().__init__(f"Provider '{provider}' failed with HTTP {status_code}: {message}")
        self.provider = provider
        self.status_code = status_code


def _is_retryable_error(exc: Exception, retryable_codes: list[int]) -> bool:
    if isinstance(exc, ProviderHTTPError):
        return exc.status_code in retryable_codes
    if isinstance(exc, TimeoutError):
        return True
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "429",
            "500",
            "502",
            "503",
            "504",
            "rate limit",
            "rate_limit",
            "overloaded",
            "timeout",
        )
    )


class DeterministicFixtureBackend:
    """Deterministic LLM backend used for offline runs, CI, and simulated provider error tests."""

    def __init__(
        self,
        provider_name: str,
        model_id: str,
        *,
        responder: Callable[[list[BaseMessage], str, str], str] | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.model_id = model_id
        self.responder = responder

    def call(self, messages: list[BaseMessage]) -> str:
        if self.responder is not None:
            return self.responder(messages, self.provider_name, self.model_id)
        last_content = str(messages[-1].content) if messages else ""
        return (
            f"[{self.provider_name}:{self.model_id}] Structured analysis complete for input "
            f"({len(last_content)} chars)."
        )


def _build_live_or_fixture_callable(
    endpoint: ProviderEndpointConfig,
    config: AppConfig,
    custom_responder: Callable[[list[BaseMessage], str, str], str] | None = None,
) -> Callable[[list[BaseMessage]], str]:
    """Build a callable that invokes either a live LangChain chat model or deterministic fixture."""
    use_mock = config.env.use_mock_llm.lower().strip()
    if custom_responder is not None or use_mock in {"true", "1", "yes"}:
        backend = DeterministicFixtureBackend(
            endpoint.provider, endpoint.model_id, responder=custom_responder
        )
        return backend.call

    if endpoint.provider == "groq" and config.env.groq_api_key:
        from langchain_groq import ChatGroq

        groq_client = ChatGroq(
            model=endpoint.model_id,
            temperature=endpoint.temperature,
            max_tokens=endpoint.max_output_tokens,
            timeout=endpoint.timeout_seconds,
            max_retries=0,
            api_key=config.env.groq_api_key,  # type: ignore[arg-type]
        )

        def _call_groq(messages: list[BaseMessage]) -> str:
            res = groq_client.invoke(messages)
            return str(res.content)

        return _call_groq

    if endpoint.provider == "gemini" and config.env.gemini_api_key:
        from langchain_google_genai import ChatGoogleGenerativeAI

        gemini_client = ChatGoogleGenerativeAI(
            model=endpoint.model_id,
            temperature=endpoint.temperature,
            max_output_tokens=endpoint.max_output_tokens,
            timeout=endpoint.timeout_seconds,
            max_retries=0,
            google_api_key=config.env.gemini_api_key,
        )

        def _call_gemini(messages: list[BaseMessage]) -> str:
            res = gemini_client.invoke(messages)
            return str(res.content)

        return _call_gemini

    # Default to deterministic fixture when API keys are not set in environment
    backend = DeterministicFixtureBackend(endpoint.provider, endpoint.model_id)
    return backend.call


class ResilientTieredChatModel(BaseChatModel):
    """LangChain `BaseChatModel` with retry/backoff, primary->secondary fallback, and tracing."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    role: ModelRole
    subagent_name: str = Field(default="subagent")
    primary_config: ProviderEndpointConfig
    secondary_config: ProviderEndpointConfig
    max_retries: int = Field(default=2)
    base_delay_seconds: float = Field(default=0.05)
    max_delay_seconds: float = Field(default=1.0)
    jitter_seconds: float = Field(default=0.02)
    retryable_status_codes: list[int] = Field(default_factory=lambda: [429, 500, 502, 503, 504])

    _primary_fn: Callable[[list[BaseMessage]], str] = PrivateAttr()
    _secondary_fn: Callable[[list[BaseMessage]], str] = PrivateAttr()
    _tracer: LLMTracer | None = PrivateAttr(default=None)
    _sleep_fn: Callable[[float], None] = PrivateAttr(default=time.sleep)

    def __init__(
        self,
        *,
        role: ModelRole,
        subagent_name: str,
        primary_config: ProviderEndpointConfig,
        secondary_config: ProviderEndpointConfig,
        primary_fn: Callable[[list[BaseMessage]], str],
        secondary_fn: Callable[[list[BaseMessage]], str],
        tracer: LLMTracer | None = None,
        max_retries: int = 2,
        base_delay_seconds: float = 0.05,
        max_delay_seconds: float = 1.0,
        jitter_seconds: float = 0.02,
        retryable_status_codes: list[int] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        init_kwargs: dict[str, Any] = {
            "role": role,
            "subagent_name": subagent_name,
            "primary_config": primary_config,
            "secondary_config": secondary_config,
            "max_retries": max_retries,
            "base_delay_seconds": base_delay_seconds,
            "max_delay_seconds": max_delay_seconds,
            "jitter_seconds": jitter_seconds,
            "retryable_status_codes": retryable_status_codes or [429, 500, 502, 503, 504],
        }
        super().__init__(**init_kwargs)
        self._primary_fn = primary_fn
        self._secondary_fn = secondary_fn
        self._tracer = tracer
        self._sleep_fn = sleep_fn

    @property
    def _llm_type(self) -> str:
        return f"resilient-{self.role.value}"

    def _invoke_with_retry(
        self,
        fn: Callable[[list[BaseMessage]], str],
        endpoint: ProviderEndpointConfig,
        messages: list[BaseMessage],
        incident_id: str,
    ) -> str:
        last_exc: Exception | None = None
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            try:
                return fn(messages)
            except Exception as exc:
                last_exc = exc
                is_retryable = _is_retryable_error(exc, self.retryable_status_codes)
                if not is_retryable or attempt >= self.max_retries:
                    break
                delay = min(
                    self.max_delay_seconds,
                    self.base_delay_seconds * (2**attempt)
                    + random.uniform(0.0, self.jitter_seconds),
                )
                log_event(
                    logger,
                    logging.WARNING,
                    "Transient provider error; retrying with exponential backoff",
                    incident_id=incident_id,
                    provider=endpoint.provider,
                    model=endpoint.model_id,
                    attempt=attempt + 1,
                    max_retries=self.max_retries,
                    backoff_seconds=round(delay, 4),
                    error=str(exc),
                )
                self._sleep_fn(delay)
        assert last_exc is not None
        raise last_exc

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        incident_id = get_incident_context()
        start_perf = time.perf_counter()
        prompt_text = "\n".join(str(m.content) for m in messages)
        input_tokens = estimate_token_count(prompt_text)

        used_endpoint = self.primary_config
        fallback_used = False
        fallback_reason: str | None = None

        try:
            response_text = self._invoke_with_retry(
                self._primary_fn, self.primary_config, messages, incident_id
            )
        except Exception as primary_exc:
            fallback_used = True
            fallback_reason = str(primary_exc)
            used_endpoint = self.secondary_config
            log_event(
                logger,
                logging.WARNING,
                "Primary LLM provider failed after retries; falling back to secondary provider",
                incident_id=incident_id,
                role=self.role.value,
                subagent=self.subagent_name,
                from_provider=self.primary_config.provider,
                from_model=self.primary_config.model_id,
                to_provider=self.secondary_config.provider,
                to_model=self.secondary_config.model_id,
                error=fallback_reason,
            )
            response_text = self._invoke_with_retry(
                self._secondary_fn, self.secondary_config, messages, incident_id
            )

        latency_ms = (time.perf_counter() - start_perf) * 1000.0
        output_tokens = estimate_token_count(response_text)

        if self._tracer is not None:
            self._tracer.record_call(
                incident_id=incident_id,
                subagent_name=self.subagent_name,
                role=self.role,
                provider=used_endpoint.provider,
                model_name=used_endpoint.model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                input_cost_per_1m_usd=used_endpoint.input_cost_per_1m_usd,
                output_cost_per_1m_usd=used_endpoint.output_cost_per_1m_usd,
                fallback_used=fallback_used,
                fallback_reason=fallback_reason,
            )

        ai_msg = AIMessage(
            content=response_text,
            response_metadata={
                "provider": used_endpoint.provider,
                "model_name": used_endpoint.model_id,
                "role": self.role.value,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": round(latency_ms, 2),
                "fallback_used": fallback_used,
                "fallback_reason": fallback_reason,
            },
        )
        return ChatResult(generations=[ChatGeneration(message=ai_msg)])

    def invoke_text(self, prompt: str) -> AIMessage:
        """Convenience helper to invoke the chat model with a string prompt."""
        res = self.invoke([HumanMessage(content=prompt)])
        if isinstance(res, AIMessage):
            return res
        return AIMessage(content=str(res.content))


def select_role_for_tokens(
    default_role: ModelRole | str,
    estimated_tokens: int,
    config: AppConfig | None = None,
) -> ModelRole:
    """Route to `long_context` (Gemini Flash) only when token count exceeds threshold."""
    cfg = config or get_config()
    role_enum = ModelRole(default_role) if isinstance(default_role, str) else default_role
    if estimated_tokens > cfg.thresholds.investigation.long_context_token_threshold:
        return ModelRole.LONG_CONTEXT
    return role_enum


def get_chat_model(
    role: ModelRole | str,
    *,
    subagent_name: str = "subagent",
    estimated_tokens: int = 0,
    tracer: LLMTracer | None = None,
    config: AppConfig | None = None,
    primary_override: Callable[[list[BaseMessage]], str] | None = None,
    secondary_override: Callable[[list[BaseMessage]], str] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> ResilientTieredChatModel:
    """Return a tiered LangChain chat model (`supervisor`, `core`, `long_context`) with fallback."""
    cfg = config or get_config()
    resolved_role = select_role_for_tokens(role, estimated_tokens, cfg)
    role_cfg = cfg.models.roles[resolved_role.value]
    retry_cfg = cfg.thresholds.retry_policy

    primary_fn = primary_override or _build_live_or_fixture_callable(role_cfg.primary, cfg)
    secondary_fn = secondary_override or _build_live_or_fixture_callable(role_cfg.secondary, cfg)

    return ResilientTieredChatModel(
        role=resolved_role,
        subagent_name=subagent_name,
        primary_config=role_cfg.primary,
        secondary_config=role_cfg.secondary,
        primary_fn=primary_fn,
        secondary_fn=secondary_fn,
        tracer=tracer,
        max_retries=retry_cfg.max_retries,
        base_delay_seconds=retry_cfg.base_delay_seconds,
        max_delay_seconds=retry_cfg.max_delay_seconds,
        jitter_seconds=retry_cfg.jitter_seconds,
        retryable_status_codes=retry_cfg.retryable_status_codes,
        sleep_fn=sleep_fn,
    )
