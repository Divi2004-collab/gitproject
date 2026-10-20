"""Typed configuration loading for the analytics application."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json
import os


@dataclass(frozen=True)
class RiskSettings:
    """Thresholds used by explainable anomaly rules.

    Amount thresholds are evaluated in the transaction's reporting currency.
    They are intentionally configurable because a useful threshold for a
    retail account may be noisy for a corporate account.
    """

    odd_hour_start: int = 6
    odd_hour_end: int = 22
    robust_mad_multiplier: float = 3.0
    minimum_amount_threshold: float = 100_000.0
    quantile_fallback: float = 0.95
    rapid_transaction_minutes: int = 5
    rapid_transaction_count: int = 4
    new_payee_amount: float = 50_000.0
    risk_medium_score: int = 35
    risk_high_score: int = 70
    risk_critical_score: int = 90

    def validate(self) -> None:
        if not 0 <= self.odd_hour_start <= 23:
            raise ValueError("odd_hour_start must be between 0 and 23")
        if not 1 <= self.odd_hour_end <= 24:
            raise ValueError("odd_hour_end must be between 1 and 24")
        if self.odd_hour_start == self.odd_hour_end:
            raise ValueError("odd-hour window cannot have identical boundaries")
        if self.robust_mad_multiplier <= 0:
            raise ValueError("robust_mad_multiplier must be positive")
        if self.minimum_amount_threshold < 0:
            raise ValueError("minimum_amount_threshold cannot be negative")
        if not 0 < self.quantile_fallback <= 1:
            raise ValueError("quantile_fallback must be in (0, 1]")
        if self.rapid_transaction_minutes < 1:
            raise ValueError("rapid_transaction_minutes must be positive")
        if self.rapid_transaction_count < 2:
            raise ValueError("rapid_transaction_count must be at least 2")
        if self.risk_medium_score >= self.risk_high_score:
            raise ValueError("medium risk score must be below high risk score")
        if self.risk_high_score >= self.risk_critical_score:
            raise ValueError("high risk score must be below critical risk score")


@dataclass(frozen=True)
class RecoverySettings:
    """Cheque follow-up bands and service-level expectations."""

    aging_bands: tuple[int, ...] = (30, 60, 90)
    follow_up_sla_days: int = 7
    reminder_grace_days: int = 3
    partial_recovery_status: str = "Partially recovered"
    bounced_status: str = "Bounced"
    recovered_status: str = "Recovered"
    pending_status: str = "Pending"
    disputed_status: str = "Disputed"
    default_owner: str = "Unassigned"
    send_messages_automatically: bool = False

    def validate(self) -> None:
        if not self.aging_bands:
            raise ValueError("at least one aging band is required")
        if tuple(sorted(set(self.aging_bands))) != self.aging_bands:
            raise ValueError("aging_bands must be unique and ascending")
        if any(day <= 0 for day in self.aging_bands):
            raise ValueError("aging bands must be positive day counts")
        if self.follow_up_sla_days < 0:
            raise ValueError("follow_up_sla_days cannot be negative")
        if self.reminder_grace_days < 0:
            raise ValueError("reminder_grace_days cannot be negative")
        if self.send_messages_automatically:
            raise ValueError("automatic customer messaging is disabled by design")


@dataclass(frozen=True)
class Settings:
    """Application paths and policy settings."""

    root: Path
    transactions_path: Path
    cheques_path: Path
    output_path: Path
    risk: RiskSettings = field(default_factory=RiskSettings)
    recovery: RecoverySettings = field(default_factory=RecoverySettings)
    timezone: str = "Asia/Kolkata"
    reporting_currency: str = "INR"
    pii_masking_enabled: bool = True
    max_upload_mb: int = 25
    log_level: str = "INFO"

    def validate(self) -> None:
        self.risk.validate()
        self.recovery.validate()
        if self.max_upload_mb <= 0:
            raise ValueError("max_upload_mb must be positive")
        if len(self.reporting_currency) != 3:
            raise ValueError("reporting_currency must be a three-letter code")


def _merge_dataclass(cls: type, values: dict[str, Any] | None):
    """Build one policy dataclass from a partial configuration mapping."""
    if not values:
        result = cls()
    else:
        allowed = cls.__dataclass_fields__.keys()
        accepted = {key: value for key, value in values.items() if key in allowed}
        if cls is RecoverySettings and "aging_bands" in accepted:
            accepted["aging_bands"] = tuple(accepted["aging_bands"])
        result = cls(**accepted)
    result.validate()
    return result


def load_settings(root: str | Path | None = None,
                  config_file: str | Path | None = None) -> Settings:
    """Load settings from defaults, a JSON file, and environment overrides.

    The precedence is environment, config file, then defaults. File paths in a
    config are resolved relative to the project root so the same setup works
    from different current working directories.
    """
    project_root = Path(root or Path(__file__).resolve().parents[1]).resolve()
    config: dict[str, Any] = {}
    path = Path(config_file) if config_file else project_root / "config" / "settings.json"
    if not path.is_absolute():
        path = project_root / path
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            config = json.load(handle)
    paths = config.get("paths", {})

    def resolve_path(key: str, default: str) -> Path:
        value = os.getenv(f"BANK_DASH_{key.upper()}", paths.get(key, default))
        candidate = Path(value)
        return (candidate if candidate.is_absolute() else project_root / candidate).resolve()

    risk_values = dict(config.get("risk", {}))
    recovery_values = dict(config.get("recovery", {}))
    for name in RiskSettings.__dataclass_fields__:
        env = os.getenv(f"BANK_DASH_RISK_{name.upper()}")
        if env is not None:
            risk_values[name] = type(getattr(RiskSettings(), name))(env)
    settings = Settings(
        root=project_root,
        transactions_path=resolve_path("transactions", "data/transactions.csv"),
        cheques_path=resolve_path("cheques", "data/cheques.csv"),
        output_path=resolve_path("output", "output"),
        risk=_merge_dataclass(RiskSettings, risk_values),
        recovery=_merge_dataclass(RecoverySettings, recovery_values),
        timezone=os.getenv("BANK_DASH_TIMEZONE", config.get("timezone", "Asia/Kolkata")),
        reporting_currency=os.getenv(
            "BANK_DASH_CURRENCY", config.get("reporting_currency", "INR")
        ),
        pii_masking_enabled=config.get("pii_masking_enabled", True),
        max_upload_mb=int(config.get("max_upload_mb", 25)),
        log_level=os.getenv("BANK_DASH_LOG_LEVEL", config.get("log_level", "INFO")),
    )
    settings.validate()
    return settings
