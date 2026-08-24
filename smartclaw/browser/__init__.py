"""SmartClaw browser runtime package."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AGENT_WORKSPACE = PROJECT_ROOT / ".smartclaw" / "plugins" / "skills" / "browser-use" / "agent-workspace"
BROWSER_LABEL = "smartclaw browser"
INTERNAL_URL_PREFIXES = (
    "about:",
    "devtools://",
    "chrome://",
    "chrome-untrusted://",
    "chrome-extension://",
    "edge://",
    "edge-untrusted://",
    "brave://",
    "brave-untrusted://",
)


def get_browser_version() -> str:
    """Return the installed SmartClaw version without a leading ``v``."""
    try:
        current = version("smartclaw")
    except PackageNotFoundError:
        current = None

    if not current:
        try:
            import tomllib

            with open(PROJECT_ROOT / "pyproject.toml", "rb") as file_obj:
                current = tomllib.load(file_obj).get("project", {}).get("version")
        except Exception:
            current = None

    return str(current or "unknown").lstrip("v")
