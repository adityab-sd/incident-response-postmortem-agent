"""Configuration loader for YAML model/threshold configs and environment variables."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderEndpointConfig(BaseModel):
    """Configuration for a single LLM provider endpoint."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model_id: str
    context_window: int
    max_output_tokens: int
    temperature: float = 0.0
    timeout_seconds: float = 15.0
    max_retries: int = 2
    input_cost_per_1m_usd: float = 0.1
    output_cost_per_1m_usd: float = 0.2


class RoleModelConfig(BaseModel):
    """Primary and secondary provider configurations for a model tier role."""

    model_config = ConfigDict(extra="forbid")

    description: str
    primary: ProviderEndpointConfig
    secondary: ProviderEndpointConfig


class SubagentBudgetConfig(BaseModel):
    """Timeout and token budget for an individual sub-agent tool."""

    model_config = ConfigDict(extra="forbid")

    role: str
    timeout_seconds: float
    max_tokens: int
    long_context_max_tokens: int | None = None


class ModelsConfig(BaseModel):
    """Root schema for `config/models.yaml`."""

    model_config = ConfigDict(extra="forbid")

    roles: dict[str, RoleModelConfig]
    subagent_budgets: dict[str, SubagentBudgetConfig]


class InvestigationThresholds(BaseModel):
    """Thresholds controlling investigation depth and budgets."""

    model_config = ConfigDict(extra="forbid")

    min_hypothesis_confidence: float = 0.70
    max_investigation_rounds: int = 3
    max_tool_calls_per_incident: int = 25
    max_tokens_per_incident: int = 100000
    long_context_token_threshold: int = 24000
    telemetry_lookback_minutes: int = 60
    telemetry_lookahead_minutes: int = 15
    change_lookback_minutes: int = 120
    triage_sla_seconds: int = 120


class RetryPolicyConfig(BaseModel):
    """Exponential backoff and jitter retry parameters."""

    model_config = ConfigDict(extra="forbid")

    max_retries: int = 2
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 5.0
    jitter_seconds: float = 0.25
    retryable_status_codes: list[int] = Field(default_factory=lambda: [429, 500, 502, 503, 504])


class SafetyConfig(BaseModel):
    """Safety and redaction settings."""

    model_config = ConfigDict(extra="forbid")

    read_only_default: bool = True
    require_human_approval_for_actions: bool = True
    redact_emails: bool = True
    redact_tokens: bool = True
    redact_ips: bool = True
    preserve_localhost_ip: bool = True
    strip_prompt_injections: bool = True
    allowed_data_sources: list[str] = Field(
        default_factory=lambda: ["logs", "metrics", "deploys", "runbooks", "topology"]
    )


class ThresholdsConfig(BaseModel):
    """Root schema for `config/thresholds.yaml`."""

    model_config = ConfigDict(extra="forbid")

    investigation: InvestigationThresholds = Field(default_factory=InvestigationThresholds)
    retry_policy: RetryPolicyConfig = Field(default_factory=RetryPolicyConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)


class EnvSettings(BaseSettings):
    """Environment-driven secrets and runtime paths."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    groq_api_key: str = Field(default="")
    gemini_api_key: str = Field(default="")
    slack_bot_token: str = Field(default="")
    slack_signing_secret: str = Field(default="")
    slack_default_channel: str = Field(default="C01INCIDENTS")
    slack_api_base_url: str = Field(default="https://slack.com/api")
    pagerduty_api_key: str = Field(default="")
    pagerduty_webhook_secret: str = Field(default="")
    pagerduty_api_base_url: str = Field(default="https://api.pagerduty.com")
    incident_db_path: str = Field(default="./data/incidents.db")
    config_models_path: str = Field(default="./config/models.yaml")
    config_thresholds_path: str = Field(default="./config/thresholds.yaml")
    log_level: str = Field(default="INFO")
    use_mock_llm: str = Field(default="auto")


class AppConfig(BaseModel):
    """Aggregated application configuration."""

    model_config = ConfigDict(extra="forbid")

    env: EnvSettings
    models: ModelsConfig
    thresholds: ThresholdsConfig


def _resolve_config_path(relative_or_abs: str) -> Path:
    path = Path(relative_or_abs)
    if path.is_absolute() and path.exists():
        return path
    cwd_candidate = Path.cwd() / path
    if cwd_candidate.exists():
        return cwd_candidate
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / path


def load_yaml_file(path: Path) -> dict[str, Any]:
    """Read and parse a YAML configuration file safely."""
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in YAML config {path}")
    return data


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Load and cache full application configuration."""
    env = EnvSettings()
    models_path = _resolve_config_path(os.environ.get("CONFIG_MODELS_PATH", env.config_models_path))
    thresholds_path = _resolve_config_path(
        os.environ.get("CONFIG_THRESHOLDS_PATH", env.config_thresholds_path)
    )
    models_data = load_yaml_file(models_path)
    thresholds_data = load_yaml_file(thresholds_path)
    return AppConfig(
        env=env,
        models=ModelsConfig.model_validate(models_data),
        thresholds=ThresholdsConfig.model_validate(thresholds_data),
    )


def clear_config_cache() -> None:
    """Clear the cached configuration (used in tests)."""
    get_config.cache_clear()
