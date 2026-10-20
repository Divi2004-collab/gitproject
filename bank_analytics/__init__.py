"""Bank transaction risk and cheque recovery analytics package.

The package separates file ingestion, validation, feature engineering,
explainable risk scoring, recovery operations, and reporting. The Streamlit
application remains a thin presentation layer over these functions.
"""

__version__ = "1.0.0"

from .config import Settings, load_settings
from .cleaning import clean_transactions, clean_cheques
from .risk import score_transactions, RiskPolicy
from .recovery import build_recovery_register, RecoveryPolicy

__all__ = [
    "Settings",
    "load_settings",
    "clean_transactions",
    "clean_cheques",
    "score_transactions",
    "RiskPolicy",
    "build_recovery_register",
    "RecoveryPolicy",
]
