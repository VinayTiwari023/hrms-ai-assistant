"""Generic REST HTTP HRMS adapter for integrating with production or external HRMS platforms."""

from contextlib import asynccontextmanager
import logging
from typing import Any, AsyncGenerator
import httpx

from app.core.config import get_settings
from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    HRMSAdapterError,
    ResourceNotFoundError,
)
from app.integrations.hrms.base import BaseHRMSAdapter
from app.schemas.hrms import (
    AttendanceToday,
    EmployeeProfile,
    HolidayItem,
    LeaveApplicationItem,
    LeaveBalance,
    MonthlyAttendanceSummary,
)

logger = logging.getLogger(__name__)


class HttpHRMSAdapter(BaseHRMSAdapter):
    """Production-ready generic HTTP adapter connecting to external REST HRMS services."""

    def __init__(
        self,
        base_url: str | None = None,
        auth_token: str | None = None,
        timeout: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.hrms_api_base_url).rstrip("/")
        self.auth_token = auth_token
        self.timeout = timeout or settings.hrms_api_timeout_seconds
        self._external_client = client

    def _get_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    @asynccontextmanager
    async def _get_client(self) -> AsyncGenerator[httpx.AsyncClient, None]:
        if self._external_client is not None and not self._external_client.is_closed:
            yield self._external_client
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as temp_client:
                yield temp_client

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{endpoint}"
        headers = self._get_headers()

        try:
            async with self._get_client() as client:
                res = await client.request(method, url, params=params, headers=headers)

            if res.status_code == 401:
                raise AuthenticationError("Upstream HRMS authentication failed or token expired.")
            elif res.status_code == 403:
                raise AuthorizationError("Access denied by upstream HRMS service.")
            elif res.status_code == 404:
                raise ResourceNotFoundError(f"Requested resource not found at {endpoint}.")
            elif res.is_error:
                raise HRMSAdapterError(
                    f"Upstream HRMS returned HTTP {res.status_code}: {res.text}",
                    code="HRMS_UPSTREAM_ERROR",
                )

            return res.json()

        except httpx.TimeoutException as exc:
            logger.error("Upstream HRMS request timed out at %s after %ss", endpoint, self.timeout)
            raise HRMSAdapterError(
                f"HRMS service timed out after {self.timeout} seconds.",
                code="HRMS_TIMEOUT",
            ) from exc
        except httpx.ConnectError as exc:
            logger.error("Cannot connect to upstream HRMS at %s", self.base_url)
            raise HRMSAdapterError(
                f"Cannot connect to HRMS service at {self.base_url}.",
                code="HRMS_CONNECTION_ERROR",
            ) from exc
        except (AuthenticationError, AuthorizationError, ResourceNotFoundError, HRMSAdapterError):
            raise
        except Exception as exc:
            logger.error("Unexpected error during HRMS HTTP request to %s: %s", endpoint, exc)
            raise HRMSAdapterError(f"HRMS HTTP communication failure: {exc}") from exc

    async def get_profile(self, employee_id: str) -> EmployeeProfile:
        data = await self._request("GET", f"/api/v1/employees/{employee_id.strip()}")
        return EmployeeProfile(**data)

    async def list_employees(self, department: str | None = None) -> list[EmployeeProfile]:
        params = {"department": department} if department else None
        data = await self._request("GET", "/api/v1/employees", params=params)
        return [EmployeeProfile(**item) for item in data]

    async def get_attendance_today(self, employee_id: str) -> AttendanceToday:
        data = await self._request("GET", f"/api/v1/employees/{employee_id.strip()}/attendance/today")
        return AttendanceToday(**data)

    async def get_monthly_attendance(
        self, employee_id: str, year: int, month: int
    ) -> MonthlyAttendanceSummary:
        params = {"year": year, "month": month}
        data = await self._request(
            "GET", f"/api/v1/employees/{employee_id.strip()}/attendance/monthly", params=params
        )
        return MonthlyAttendanceSummary(**data)

    async def get_leave_balance(self, employee_id: str, year: int | None = None) -> LeaveBalance:
        params = {"year": year} if year else None
        data = await self._request(
            "GET", f"/api/v1/employees/{employee_id.strip()}/leaves/balance", params=params
        )
        return LeaveBalance(**data)

    async def get_leave_applications(
        self, employee_id: str, status: str | None = None
    ) -> list[LeaveApplicationItem]:
        params = {"status": status} if status else None
        data = await self._request(
            "GET", f"/api/v1/employees/{employee_id.strip()}/leaves/applications", params=params
        )
        return [LeaveApplicationItem(**item) for item in data]

    async def get_holiday_calendar(self, year: int) -> list[HolidayItem]:
        params = {"year": year}
        data = await self._request("GET", "/api/v1/holidays", params=params)
        return [HolidayItem(**item) for item in data]

    async def health_check(self) -> bool:
        try:
            url = f"{self.base_url}/health"
            async with self._get_client() as client:
                res = await client.get(url, headers=self._get_headers())
                return res.status_code == 200
        except Exception:
            return False
