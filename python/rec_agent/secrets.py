from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Outside the repo — never commit these files' contents into git.
DEFAULT_LLM_API_KEY_FILE = Path(r"G:\llm apikey.txt")
DEFAULT_LLM_CONFIG_FILE = Path(r"G:\llm.config.txt")


def _parse_kv_lines(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if ":" in line and "=" not in line.split(":", 1)[0]:
            key, _, val = line.partition(":")
        elif "=" in line:
            key, _, val = line.partition("=")
        else:
            # bare key line (api key only files)
            if "api_key" not in out and "key" not in out:
                out["api_key"] = line
            continue
        k = key.strip().lower().replace("-", "_")
        v = val.strip().strip('"').strip("'")
        if k in {"api_key", "apikey", "key", "llm_api_key"}:
            out["api_key"] = v
        elif k in {"base_url", "llm_base_url", "api_base", "base"}:
            out["base_url"] = v
        elif k in {"model", "llm_model"}:
            out["model"] = v
    return out


def load_llm_api_key(
    *,
    explicit: str | None = None,
    key_file: str | Path | None = None,
    allow_default_file: bool = False,
) -> str:
    """Resolve API key from explicit value, env, or external file (not in-repo)."""
    cfg = load_llm_external_config(
        key_file=key_file,
        allow_default_files=allow_default_file,
    )
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()

    env_key = os.environ.get("REC_AGENT_LLM_API_KEY", "").strip()
    if env_key:
        return env_key

    return (cfg.get("api_key") or "").strip()


def load_llm_external_config(
    *,
    key_file: str | Path | None = None,
    config_file: str | Path | None = None,
    allow_default_files: bool = False,
) -> dict[str, str]:
    """Load api_key / base_url / model from external files only (never from repo)."""
    merged: dict[str, str] = {}

    paths: list[Path] = []
    if config_file is not None and str(config_file).strip():
        paths.append(Path(str(config_file).strip()))
    else:
        env_cfg = os.environ.get("REC_AGENT_LLM_CONFIG_FILE", "").strip()
        if env_cfg:
            paths.append(Path(env_cfg))
        elif allow_default_files and DEFAULT_LLM_CONFIG_FILE.is_file():
            paths.append(DEFAULT_LLM_CONFIG_FILE)

    if key_file is not None and str(key_file).strip():
        paths.append(Path(str(key_file).strip()))
    else:
        env_key_file = os.environ.get("REC_AGENT_LLM_API_KEY_FILE", "").strip()
        if env_key_file:
            paths.append(Path(env_key_file))
        elif allow_default_files and DEFAULT_LLM_API_KEY_FILE.is_file():
            paths.append(DEFAULT_LLM_API_KEY_FILE)

    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        parsed = _parse_kv_lines(text)
        # later files override earlier for same keys only if non-empty
        for k, v in parsed.items():
            if v:
                merged[k] = v
        # single-line secret file with no kv → treat whole file as key
        if "api_key" not in parsed and "\n" not in text and text.startswith("sk-"):
            merged["api_key"] = text.strip()
    return merged


def redact_secret(text: str, secret: str) -> str:
    if not secret or not text:
        return text
    return text.replace(secret, "***")
