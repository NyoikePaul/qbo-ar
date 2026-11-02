"""Runtime settings, loaded from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

BASE_URLS = {
    "sandbox": "https://sandbox-quickbooks.api.intuit.com",
    "production": "https://quickbooks.api.intuit.com",
}
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
DEFAULT_MINOR_VERSION = 75


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    realm_id: str
    environment: str = "sandbox"
    minor_version: int = DEFAULT_MINOR_VERSION
    token_path: Path = Path("~/.qbo_ar/tokens.json")
    timeout: float = 30.0
    max_retries: int = 5

    def __post_init__(self) -> None:
        if self.environment not in BASE_URLS:
            raise ConfigError(
                f"environment must be one of {sorted(BASE_URLS)}, got {self.environment!r}"
            )
        object.__setattr__(self, "token_path", Path(self.token_path).expanduser())

    @property
    def company_url(self) -> str:
        return f"{BASE_URLS[self.environment]}/v3/company/{self.realm_id}"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        required = ("QBO_CLIENT_ID", "QBO_CLIENT_SECRET", "QBO_REALM_ID")
        missing = [name for name in required if not env.get(name)]
        if missing:
            raise ConfigError("Missing environment variables: " + ", ".join(missing))
        return cls(
            client_id=env["QBO_CLIENT_ID"],
            client_secret=env["QBO_CLIENT_SECRET"],
            realm_id=env["QBO_REALM_ID"],
            environment=env.get("QBO_ENVIRONMENT", "sandbox"),
            minor_version=int(env.get("QBO_MINOR_VERSION", DEFAULT_MINOR_VERSION)),
            token_path=Path(env.get("QBO_TOKEN_PATH", "~/.qbo_ar/tokens.json")),
        )
