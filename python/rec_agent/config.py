from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from rec_agent.secrets import (
    DEFAULT_LLM_API_KEY_FILE,
    DEFAULT_LLM_CONFIG_FILE,
    load_llm_external_config,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="REC_AGENT_",
        env_file=".env",
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    use_mock_llm: bool = True
    llm_api_key: str = ""
    # Outside-repo paths (e.g. G:\llm apikey.txt). Empty = do not read files.
    llm_api_key_file: str = ""
    llm_config_file: str = ""
    llm_base_url: str = ""
    llm_model: str = "mock"
    llm_timeout_s: float = 30.0
    llm_temperature: float = 0.0
    task_timeout_ms: int = 20_000
    model_min_remaining_ms: int = 500
    enable_react: bool = True
    enable_llm: bool = True
    force_route: str = ""  # SKIP|FALLBACK|RULE_ENGINE|DIRECT|REACT for eval

    # Policy defaults — marked as assumptions in docs/待确认.md
    freq_cap_per_day: int = 10
    freq_window_hours: int = 24
    cooldown_minutes: int = 0
    similar_suppress_minutes: int = 120
    scene_dedupe_minutes: int = 60
    exposure_history_window_hours: int = 72
    not_interested_ttl_days: int = 30
    dnd_start_hour: int = 23
    dnd_end_hour: int = 7

    max_merchants: int = 3
    max_products_per_merchant: int = 3
    max_same_category: int = 2
    candidate_limit: int = 30
    recall_limit: int = 20
    react_max_steps: int = 6
    max_submit_failures: int = 2
    tool_timeout_ms: int = 2000
    supplement_timeout_ms: int = 5000
    supplement_min: int = 3
    # Evidence about to expire within this window → need_freshness (0 = only missing/expired)
    freshness_horizon_minutes: int = 0
    # When set, submit self_score below this rejects the draft (counts toward max_submit_failures)
    display_confidence_threshold: float | None = None
    # When set, opportunity heuristic score below this → suppress (reason=low_opportunity_score)
    opportunity_min_score: float | None = None

    @model_validator(mode="after")
    def _resolve_llm_secrets(self) -> "Settings":
        key_file = self.llm_api_key_file.strip() if self.llm_api_key_file else ""
        cfg_file = self.llm_config_file.strip() if self.llm_config_file else ""
        external = load_llm_external_config(
            key_file=key_file or None,
            config_file=cfg_file or None,
            allow_default_files=False,
        )
        key = (self.llm_api_key or "").strip() or (external.get("api_key") or "").strip()
        if not key:
            # env-only fallback (REC_AGENT_LLM_API_KEY / REC_AGENT_LLM_API_KEY_FILE)
            external_env = load_llm_external_config(allow_default_files=False)
            key = (external_env.get("api_key") or "").strip()
            for k in ("base_url", "model"):
                if external_env.get(k) and k not in external:
                    external[k] = external_env[k]
        object.__setattr__(self, "llm_api_key", key)

        base = (self.llm_base_url or "").strip() or (external.get("base_url") or "").strip()
        model = (self.llm_model or "").strip()
        if not model or model == "mock":
            model = (external.get("model") or "").strip() or ("gpt-4o-mini" if key else "mock")
        if key and not base:
            base = "https://api.openai.com/v1"
        if base:
            object.__setattr__(self, "llm_base_url", base)
        if model:
            object.__setattr__(self, "llm_model", model)
        return self

    def llm_ready(self) -> bool:
        return bool(self.llm_api_key and self.llm_base_url and self.llm_model not in {"", "mock"})


def live_llm_settings(**overrides) -> Settings:
    """Settings for real tool-calling (key/config from external files). Does not enable mock."""
    base = {
        "use_mock_llm": False,
        "llm_api_key_file": str(DEFAULT_LLM_API_KEY_FILE),
        "llm_config_file": str(DEFAULT_LLM_CONFIG_FILE),
        "cooldown_minutes": 0,
        "scene_dedupe_minutes": 0,
        "enable_llm": True,
        "enable_react": True,
        # Real providers are slower than mock; keep headroom for tool loops.
        "task_timeout_ms": 120_000,
        "llm_timeout_s": 60.0,
        "react_max_steps": 8,
        "max_submit_failures": 2,
    }
    base.update(overrides)
    return Settings(**base)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
