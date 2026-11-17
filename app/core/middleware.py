"""Application middleware for request correlation, tracing, and timing."""

import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.observability import set_current_request_id


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Assigns or propagates X-Request-ID across the HTTP request lifecycle."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Check incoming header or generate new UUID
        request_id = request.headers.get("X-Request-ID")
        if not request_id or not request_id.strip():
            request_id = str(uuid.uuid4())
        else:
            request_id = request_id.strip()

        # Bind to request state and task context variable
        request.state.request_id = request_id
        set_current_request_id(request_id)

        start_time = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        # Inject into response headers
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-MS"] = f"{duration_ms:.2f}"

        return response
