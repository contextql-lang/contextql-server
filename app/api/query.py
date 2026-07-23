import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.dependencies import get_query_service
from app.models.request import QueryRequest
from app.models.response import ErrorDetail, QueryResponse
from app.services.query_service import QueryService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/query", response_model=QueryResponse)
def query(
    request: QueryRequest,
    service: QueryService = Depends(get_query_service),
) -> QueryResponse | JSONResponse:
    try:
        return service.execute_response(request.query)
    except ValueError as exc:
        logger.warning("Query validation error: %s", exc)
        return JSONResponse(
            status_code=400,
            content=ErrorDetail(
                error="bad_request",
                message=str(exc),
            ).model_dump(),
        )
    except Exception as exc:
        logger.exception("Unexpected error during query execution")
        return JSONResponse(
            status_code=500,
            content=ErrorDetail(
                error="internal_error",
                message="An unexpected error occurred",
                detail=str(exc),
            ).model_dump(),
        )
