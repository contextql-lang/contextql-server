from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.dependencies import get_query_service
from app.models.explain import (
    ExplainRequest,
    ExplainResponse,
    ProviderCallDetail,
    TraceResponse,
)
from app.models.response import ErrorDetail
from app.services.query_service import QueryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/query", tags=["query"])


@router.post("/explain", response_model=ExplainResponse)
def explain_query(
    body: ExplainRequest,
    service: QueryService = Depends(get_query_service),
) -> ExplainResponse | JSONResponse:
    try:
        result = service.execute(body.query)

        trace_data = TraceResponse(
            contexts_resolved=[],
            provider_calls=[],
            identity_maps_used=[],
            score_breakdown={},
        )

        if result.trace is not None:
            provider_calls = []
            for pc in getattr(result.trace, "provider_calls", []):
                provider_calls.append(ProviderCallDetail(
                    provider_name=pc.provider_name,
                    provider_type=pc.provider_type,
                    entity_count=pc.entity_count,
                    elapsed_ms=pc.elapsed_ms,
                    data_as_of=getattr(pc, "data_as_of", None),
                ))
            trace_data = TraceResponse(
                contexts_resolved=getattr(result.trace, "contexts_resolved", []),
                provider_calls=provider_calls,
                identity_maps_used=getattr(result.trace, "identity_maps_used", []),
                score_breakdown=getattr(result.trace, "score_breakdown", {}),
            )

        return ExplainResponse(
            rows=result.rows,
            meta=result.meta,
            trace=trace_data,
        )
    except Exception as exc:
        # Treat parse/validation errors as 400, everything else as 500
        exc_name = type(exc).__name__
        if isinstance(exc, ValueError) or "Syntax" in exc_name or "Parse" in exc_name:
            logger.warning("Explain validation error: %s", exc)
            return JSONResponse(
                status_code=400,
                content=ErrorDetail(
                    error="bad_request",
                    message=str(exc),
                ).model_dump(),
            )
        logger.exception("Unexpected error during explain execution")
        return JSONResponse(
            status_code=500,
            content=ErrorDetail(
                error="internal_error",
                message="An unexpected error occurred",
                detail=str(exc),
            ).model_dump(),
        )
