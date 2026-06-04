"""Session prompt locale resolution."""

import locale
import os
from typing import Optional


def normalize_prompt_locale(value: Optional[str]) -> str:
    normalized = (value or "").strip().lower().replace("_", "-")
    return "zh-CN" if normalized.startswith("zh") else "en-US"


def get_prompt_locale() -> str:
    """Resolve the session prompt locale.

    Defaults to English so existing deployments keep the original prompt behavior.
    Set FLOCKS_SESSION_PROMPT_LOCALE=zh-CN to prefer *.zh.txt prompt variants.
    """
    explicit = os.getenv("FLOCKS_SESSION_PROMPT_LOCALE") or os.getenv("FLOCKS_PROMPT_LOCALE")
    if explicit:
        return normalize_prompt_locale(explicit)

    if os.getenv("FLOCKS_SESSION_PROMPT_AUTO_LOCALE", "").lower() in {"1", "true", "yes"}:
        env_locale = os.getenv("FLOCKS_LOCALE") or os.getenv("FLOCKS_LANGUAGE") or os.getenv("FLOCKS_INSTALL_LANGUAGE")
        if env_locale:
            return normalize_prompt_locale(env_locale)
        try:
            system_locale = locale.getlocale()[0] or os.getenv("LANG")
        except Exception:
            system_locale = None
        return normalize_prompt_locale(system_locale)

    return "en-US"


def is_zh_prompt_locale() -> bool:
    return get_prompt_locale() == "zh-CN"
