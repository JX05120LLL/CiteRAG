"""Sanitized provider failures safe for logs, reports, and API state."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderDiagnostic:
    """Fixed labels derived from a provider error; never contains response text."""

    response_shape: str
    code_class: str
    message_class: str
    input_range: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if self.response_shape not in {
            "non_json", "json_other", "error_object", "error_other", "top_level"
        }:
            raise ValueError("invalid provider response shape")
        if self.code_class not in {
            "missing", "invalid_parameter", "compatible_parameter", "other"
        }:
            raise ValueError("invalid provider code class")
        if self.message_class not in {
            "missing", "input_range", "input_length_generic", "dimensions",
            "batch_size", "input_format", "other",
        }:
            raise ValueError("invalid provider message class")
        if self.input_range is not None and (
            self.message_class != "input_range"
            or len(self.input_range) != 2
            or any(type(bound) is not int or not 0 <= bound <= 99999 for bound in self.input_range)
        ):
            raise ValueError("invalid provider input range")


class ProviderError(RuntimeError):
    def __init__(
        self,
        category: str,
        provider: str,
        model: str,
        *,
        status: int | None = None,
        request_id: str | None = None,
        diagnostic: ProviderDiagnostic | None = None,
    ) -> None:
        self.category = category
        self.provider = provider
        self.model = model
        self.status = status
        self.request_id = request_id
        self.diagnostic = diagnostic
        super().__init__(self._safe_message())

    def _safe_message(self) -> str:
        details = [f"provider={self.provider}", f"model={self.model}"]
        if self.status is not None:
            details.append(f"status={self.status}")
        return f"provider_{self.category} ({', '.join(details)})"


class ProviderBudgetExceeded(RuntimeError):
    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"provider request budget exhausted (limit={limit})")
