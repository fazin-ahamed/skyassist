from collections.abc import Callable
from datetime import datetime

from skyassist.models import (
    Applicability, ArrivalEstimates, GetArrivalEstimatesInput, ReasonCode, Success, ToolError,
    ToolErrorClass, ToolResult,
)

type ArrivalHook = Callable[[GetArrivalEstimatesInput, datetime], object]


def _shape(value: object) -> ArrivalEstimates:
    estimates = value if isinstance(value, ArrivalEstimates) else ArrivalEstimates.model_validate(value)
    for estimate in (estimates.baggage, estimates.immigration, estimates.security):
        if estimate is None:
            continue
        if estimate.provenance is None:
            raise ValueError("each estimate needs a source and timestamp")
        if estimate.wait is not None and estimate.applicability is not Applicability.APPLICABLE:
            raise ValueError("a wait needs an applicable estimate")
    return estimates


class ArrivalEstimatesHook:
    def __init__(self) -> None:
        self._hook: ArrivalHook | None = None

    def register(self, hook: ArrivalHook) -> None:
        if not callable(hook):
            raise TypeError("arrival hook must be callable")
        self._hook = hook

    def arrival_estimates(
        self, request: GetArrivalEstimatesInput, *, as_of: datetime, deadline: datetime,
    ) -> ToolResult[ArrivalEstimates]:
        if self._hook is None:
            return ToolError(error_class=ToolErrorClass.UNAVAILABLE, diagnostic=ReasonCode.ESTIMATE_UNAVAILABLE)
        try:
            estimates = _shape(self._hook(request, as_of))
        except Exception:
            return ToolError(error_class=ToolErrorClass.UNAVAILABLE, diagnostic=ReasonCode.ESTIMATE_UNAVAILABLE)
        if estimates.baggage is None and estimates.immigration is None and estimates.security is None:
            return ToolError(error_class=ToolErrorClass.MISSING, diagnostic=ReasonCode.ESTIMATE_UNAVAILABLE)
        return Success(value=estimates)
