"""Provider availability, role separation, and fallback (§54–57).

A rate-limited provider costs a retry, not the session: the PR understanding
already paid for lives in the session state, so the other provider resumes from
it instead of re-reading the whole pull request.

The parent the user named after `--` owns the visible session and its `--model`.
That model does not silently become every worker's model.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from coderai.pr_automation.state import Session, read_json, write_json

HEALTH_FILE = "provider-health.json"
PROVIDERS = ("claude", "codex")

AVAILABLE = "AVAILABLE"
TEMPORARILY_LIMITED = "TEMPORARILY_LIMITED"
AUTH_FAILED = "AUTH_FAILED"
NOT_INSTALLED = "NOT_INSTALLED"
FAILED = "FAILED"
UNKNOWN = "UNKNOWN"

TRIAGE = "triage"
VERIFY = "verify"
FIX = "fix"
REVIEW = "review"
ROLES = (TRIAGE, VERIFY, FIX, REVIEW)

# How long a provider stays out of rotation after each kind of trouble.
BACKOFF = {
    TEMPORARILY_LIMITED: 900,     # rate limit: try again in 15 minutes
    AUTH_FAILED: 3600,            # a human must log in; retrying is pointless
    FAILED: 300,                  # transient failure, exponential from here
}
MAX_BACKOFF = 3600


@dataclass
class ProviderHealth:
    name: str
    state: str = UNKNOWN
    failures: int = 0
    updated_at: float = 0.0
    retry_after: float = 0.0
    note: str = ""

    def usable(self, now: float) -> bool:
        if self.state == NOT_INSTALLED:
            return False
        if self.state in {AVAILABLE, UNKNOWN}:
            return True
        return now >= self.retry_after

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HealthStore:
    providers: dict[str, ProviderHealth] = field(default_factory=dict)

    def get(self, name: str) -> ProviderHealth:
        if name not in self.providers:
            self.providers[name] = ProviderHealth(name=name)
        return self.providers[name]

    def to_dict(self) -> dict[str, Any]:
        return {name: item.to_dict() for name, item in self.providers.items()}

    @classmethod
    def from_dict(cls, value: Any) -> "HealthStore":
        store = cls()
        if isinstance(value, dict):
            for name, item in value.items():
                if isinstance(item, dict):
                    known = {key: item[key] for key in ProviderHealth.__dataclass_fields__
                             if key in item}
                    known["name"] = str(name)
                    store.providers[str(name)] = ProviderHealth(**known)
        return store


def load(directory: Path) -> HealthStore:
    return HealthStore.from_dict(read_json(Path(directory) / HEALTH_FILE))


def save(directory: Path, store: HealthStore) -> None:
    write_json(Path(directory) / HEALTH_FILE, store.to_dict())


def installed(name: str) -> bool:
    return shutil.which(name) is not None


def detect(store: HealthStore, names: Iterable[str] = PROVIDERS,
           now: float | None = None) -> HealthStore:
    """Mark what is actually on this machine, without disturbing known trouble."""
    moment = time.time() if now is None else now
    for name in names:
        health = store.get(name)
        if not installed(name):
            health.state = NOT_INSTALLED
            health.updated_at = moment
        elif health.state in {NOT_INSTALLED, UNKNOWN}:
            health.state = AVAILABLE
            health.updated_at = moment
    return store


def record(store: HealthStore, name: str, outcome: str, *, note: str = "",
           now: float | None = None) -> ProviderHealth:
    """Record how a run ended, and when this provider may be tried again."""
    moment = time.time() if now is None else now
    health = store.get(name)
    health.updated_at = moment
    health.note = note[:300]
    if outcome == AVAILABLE:
        health.state = AVAILABLE
        health.failures = 0
        health.retry_after = 0.0
        return health
    health.state = outcome
    health.failures += 1
    base = BACKOFF.get(outcome, BACKOFF[FAILED])
    if outcome == FAILED:
        base = min(MAX_BACKOFF, base * (2 ** (health.failures - 1)))
    health.retry_after = moment + base
    return health


# What the provider CLIs actually say when they are limited or logged out.
_LIMIT_PATTERNS = (
    "rate limit", "rate_limit", "usage limit", "quota", "too many requests",
    "429", "overloaded", "capacity", "try again later", "temporarily unavailable",
    "resource_exhausted", "server_error", "503",
)
_AUTH_PATTERNS = (
    "not logged in", "unauthorized", "authentication", "auth error", "401",
    "invalid api key", "please run", "login required", "credentials",
)


def classify_failure(text: str) -> str:
    """Map a failed provider run onto a health state, so backoff fits the cause."""
    lowered = (text or "").lower()
    if any(pattern in lowered for pattern in _AUTH_PATTERNS):
        return AUTH_FAILED
    if any(pattern in lowered for pattern in _LIMIT_PATTERNS):
        return TEMPORARILY_LIMITED
    return FAILED


@dataclass(frozen=True)
class Assignment:
    provider: str
    argv: list[str]
    role: str
    fallback: bool = False
    reason: str = ""


def _argv_for(provider: str, session: Session, role: str) -> list[str]:
    """The parent's model belongs to the parent role only (§57)."""
    if role == TRIAGE and provider == session.provider and session.provider_argv:
        return list(session.provider_argv)
    return [provider]


def select(role: str, session: Session, store: HealthStore, *,
           now: float | None = None, prefer_opposite_for_review: bool = True
           ) -> Assignment | None:
    """Pick a provider for one role: capability and availability, never loyalty."""
    moment = time.time() if now is None else now
    order: list[str] = []

    if role == REVIEW and prefer_opposite_for_review:
        # §44: a second opinion is worth more from the other provider.
        order = [name for name in PROVIDERS if name != session.provider]
        order.append(session.provider)
    else:
        order = [session.provider]
        order += [name for name in PROVIDERS if name != session.provider]

    for index, name in enumerate(order):
        health = store.get(name)
        if health.state == NOT_INSTALLED or not installed(name):
            continue
        if not health.usable(moment):
            continue
        fallback = name != session.provider and role != REVIEW
        reason = ""
        if fallback:
            parent = store.get(session.provider)
            reason = f"{session.provider} is {parent.state.lower()}"
        elif role == REVIEW and name != session.provider:
            reason = "cross-provider review"
        return Assignment(provider=name, argv=_argv_for(name, session, role), role=role,
                          fallback=fallback, reason=reason)
    return None
