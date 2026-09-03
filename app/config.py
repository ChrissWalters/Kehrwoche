"""Application settings, read from environment variables."""

from __future__ import annotations

import logging
import sys
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Currency used for households that do not pick one explicitly (ISO 4217).
DEFAULT_CURRENCY = "EUR"

HTTPS_PORT = 8443
HTTP_PORT = 8080


class TlsMode(StrEnum):
    SELF_SIGNED = "self-signed"
    CUSTOM = "custom"
    OFF = "off"


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Settings(BaseSettings):
    """Instance configuration. Every field maps to an upper-case environment variable."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    database_url: str = "sqlite:////data/kehrwoche.db"
    tls_mode: TlsMode = TlsMode.SELF_SIGNED
    tls_cert_file: Path | None = None
    tls_key_file: Path | None = None
    external_hostnames: str = "kehrwoche.local"
    port: int | None = None
    session_max_age_days: int = 30
    registration_open: bool = True
    #: The address is only a login name and no mail is ever sent, so anything
    #: non-empty is accepted by default — handy for home networks ("alex@wg").
    #: Instances open to the internet can switch the format check on.
    email_validation: bool = False
    data_dir: Path = Path("/data")
    #: Optional volume with additional or overriding language files.
    locales_extra_dir: Path = Path("/app/locales-extra")
    log_level: LogLevel = LogLevel.INFO

    @model_validator(mode="after")
    def _apply_dependent_defaults(self) -> Settings:
        if self.port is None:
            self.port = HTTP_PORT if self.tls_mode is TlsMode.OFF else HTTPS_PORT
        return self

    @property
    def hostnames(self) -> list[str]:
        """`EXTERNAL_HOSTNAMES` split into single host names (certificate SANs)."""
        return [host.strip() for host in self.external_hostnames.split(",") if host.strip()]

    @property
    def tls_enabled(self) -> bool:
        return self.tls_mode is not TlsMode.OFF

    @property
    def media_dir(self) -> Path:
        """Where processed images live — inside the data volume, next to the database."""
        return self.data_dir / "media"


def configure_logging(settings: Settings) -> None:
    """Log to stdout only — the container runtime collects the stream.

    Here rather than next to the application, because the start-up sequence needs it
    before there is an application: importing ``app.main`` builds the whole thing, and
    nothing should be built until the database has been brought up to date.
    """
    logging.basicConfig(
        stream=sys.stdout,
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
