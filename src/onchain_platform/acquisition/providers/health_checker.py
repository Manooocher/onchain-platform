"""Provider health monitor (Reality Check Risk: provider downtime).

Tracks each provider's healthy/unhealthy state, marks a provider unhealthy
after `unhealthy_threshold` consecutive failures, and flips it back to healthy
after an optional recovery interval.

The health state is used by the MultiProvider orchestrator to skip unhealthy
providers during failover.
"""

import time

from onchain_platform.platform.provider_config import ChainProviderConfig


class ProviderHealthChecker:
    """Tracks availability of each configured provider."""

    def __init__(self, config: ChainProviderConfig) -> None:
        self._providers = {spec.name: spec for spec in config.providers}
        self._healthy: dict[str, bool] = {spec.name: True for spec in config.providers}
        self._consecutive_failures: dict[str, int] = {spec.name: 0 for spec in config.providers}
        self._unhealthy_threshold = config.unhealthy_threshold
        self._recovery = config.recovery_interval_seconds
        self._unhealthy_since: dict[str, float] = {}

    def is_healthy(self, provider_name: str) -> bool:
        return self._healthy.get(provider_name, False)

    def mark_unhealthy(self, provider_name: str) -> None:
        """Increment failure count; mark unhealthy once threshold reached."""
        if provider_name not in self._providers:
            return
        self._consecutive_failures[provider_name] += 1
        if (
            self._consecutive_failures[provider_name] >= self._unhealthy_threshold
            and self._healthy[provider_name]
        ):
            self._healthy[provider_name] = False
            self._unhealthy_since[provider_name] = time.monotonic()

    def mark_healthy(self, provider_name: str) -> None:
        if provider_name not in self._providers:
            return
        self._healthy[provider_name] = True
        self._consecutive_failures[provider_name] = 0
        self._unhealthy_since.pop(provider_name, None)

    def is_recovery_due(self, provider_name: str) -> bool:
        """True once an unhealthy provider's recovery interval has elapsed."""
        if provider_name not in self._providers:
            return False
        since = self._unhealthy_since.get(provider_name)
        if since is None:
            return True  # not marked unhealthy
        return time.monotonic() - since >= self._recovery
