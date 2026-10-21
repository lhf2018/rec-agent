from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="REC_AGENT_",
        env_file=".env",
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    use_mock_llm: bool = True
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = "mock"
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
    react_max_steps: int = 4
    tool_timeout_ms: int = 2000
    supplement_timeout_ms: int = 5000
    supplement_min: int = 3
    display_confidence_threshold: float | None = None  # unused as sole gate


@lru_cache
def get_settings() -> Settings:
    return Settings()
