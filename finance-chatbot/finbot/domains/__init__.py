"""Domain plug-ins. Importing this package registers the built-in ones."""
from . import (  # noqa: F401  (each registers itself)
    accounts,
    bnpl,
    disputes,
    insurance,
    issuing,
    kyc,
    lending,
    mortgages,
    onboarding,
    subscriptions,
    transfers,
)
from .base import DOMAINS, PAYMENTS, Domain, Rule, by_id, by_tool, enabled, find_ids, id_of, record_tools, register

DEFAULT = ("accounts,transfers,kyc,lending,issuing,onboarding,disputes,mortgages,bnpl,insurance,subscriptions,"
           "payments")

__all__ = ["DEFAULT", "DOMAINS", "PAYMENTS", "Domain", "Rule", "by_id", "by_tool", "enabled", "find_ids", "id_of",
           "record_tools", "register"]
