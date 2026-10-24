"""Data source allowlist and read-only enforcement."""

from __future__ import annotations


class DisallowedDataSourceError(PermissionError):
    """Raised when a query targets an unapproved data source or write mode."""


class DataSourceAllowlist:
    """Enforces that adapters only access allowlisted data sources in read-only mode."""

    def __init__(self, allowed_sources: list[str] | None = None) -> None:
        self.allowed_sources: set[str] = set(
            allowed_sources
            if allowed_sources is not None
            else ["logs", "metrics", "deploys", "runbooks", "topology"]
        )

    def verify(self, source_name: str, *, mode: str = "read") -> None:
        """Raise DisallowedDataSourceError if source or access mode is not permitted."""
        if mode.lower() != "read":
            raise DisallowedDataSourceError(
                f"Write/mutate mode '{mode}' is forbidden on data source '{source_name}'. "
                "Sub-agents are strictly read-only."
            )
        if source_name not in self.allowed_sources:
            raise DisallowedDataSourceError(
                f"Data source '{source_name}' is not in the allowlist: {sorted(self.allowed_sources)}"
            )
