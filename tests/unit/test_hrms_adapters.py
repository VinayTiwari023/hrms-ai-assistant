"""Unit tests for Mock and Generic HTTP HRMS adapters."""

import json
import pytest
import httpx

from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    HRMSAdapterError,
    ResourceNotFoundError,
)
from app.integrations.hrms.factory import create_hrms_adapter
from app.integrations.hrms.http_adapter import HttpHRMSAdapter
from app.integrations.hrms.mock_adapter import MockHRMSAdapter
from app.schemas.hrms import (
    AttendanceToday,
    EmployeeProfile,
    HolidayItem,
    LeaveApplicationItem,
    LeaveBalance,
    MonthlyAttendanceSummary,
)


# ==============================================================================
# MockHRMSAdapter Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_mock_adapter_get_profile() -> None:
    """Verify MockHRMSAdapter retrieves existing profiles and rejects unknown IDs."""
    adapter = MockHRMSAdapter()

    # 1. Existing Employee
    emp = await adapter.get_profile("EMP-1001")
    assert emp.full_name == "Alex Rivera"
    assert emp.department == "Engineering"
    assert emp.role == "employee"

    # 2. Existing HR User
    hr_emp = await adapter.get_profile("EMP-1002")
    assert hr_emp.full_name == "Sarah Chen"
    assert hr_emp.role == "hr"

    # 3. Non-existent Employee raises ResourceNotFoundError
    with pytest.raises(ResourceNotFoundError):
        await adapter.get_profile("EMP-UNKNOWN-999")


@pytest.mark.asyncio
async def test_mock_adapter_list_employees() -> None:
    """Verify MockHRMSAdapter lists all employees and filters by department."""
    adapter = MockHRMSAdapter()

    # All employees
    all_emps = await adapter.list_employees()
    assert len(all_emps) >= 3

    # Department filter
    eng_emps = await adapter.list_employees(department="Engineering")
    assert len(eng_emps) >= 1
    assert all(e.department.lower() == "engineering" for e in eng_emps)


@pytest.mark.asyncio
async def test_mock_adapter_attendance() -> None:
    """Verify MockHRMSAdapter daily and monthly attendance queries."""
    adapter = MockHRMSAdapter()

    # Daily attendance
    att = await adapter.get_attendance_today("EMP-1001")
    assert att.status == "Present"
    assert att.check_in == "09:12"

    # Monthly attendance summary
    monthly = await adapter.get_monthly_attendance("EMP-1001", year=2026, month=11)
    assert monthly.year == 2026
    assert monthly.month == 11
    assert monthly.total_working_days > 0


@pytest.mark.asyncio
async def test_mock_adapter_leaves_and_holidays() -> None:
    """Verify MockHRMSAdapter leave balances, applications, and holidays."""
    adapter = MockHRMSAdapter()

    # Leave balances
    bal = await adapter.get_leave_balance("EMP-1001", year=2026)
    assert len(bal.balances) >= 1
    annual = next(b for b in bal.balances if b.leave_type == "Annual Leave")
    assert annual.available > 0

    # Leave applications (unfiltered & filtered)
    apps = await adapter.get_leave_applications("EMP-1001")
    assert len(apps) >= 1

    approved = await adapter.get_leave_applications("EMP-1001", status="Approved")
    assert all(a.status == "Approved" for a in approved)

    # Holidays
    holidays = await adapter.get_holiday_calendar(2026)
    assert len(holidays) >= 5
    assert any(h.name == "New Year's Day" for h in holidays)

    # Health check
    assert await adapter.health_check() is True


# ==============================================================================
# HttpHRMSAdapter Tests (Using HTTPX MockTransport)
# ==============================================================================


@pytest.mark.asyncio
async def test_http_adapter_successful_operations() -> None:
    """Verify HttpHRMSAdapter maps REST JSON payloads to standardized Pydantic models."""
    sample_profile_payload = {
        "employee_id": "EMP-1001",
        "first_name": "Alex",
        "last_name": "Rivera",
        "full_name": "Alex Rivera",
        "email": "alex.rivera@novatech-fictional.internal",
        "department": "Engineering",
        "designation": "Software Engineer",
        "role": "employee",
        "joining_date": "2023-03-15",
        "reporting_manager_id": "EMP-1003",
        "employment_status": "active",
    }

    sample_att_payload = {
        "employee_id": "EMP-1001",
        "date": "2026-11-16",
        "status": "Present",
        "check_in": "09:15",
        "check_out": None,
        "work_minutes": 240,
        "is_late": False,
        "late_minutes": 0,
    }

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/api/v1/employees/EMP-1001/attendance/today" in url:
            return httpx.Response(200, json=sample_att_payload)
        elif "/api/v1/employees/EMP-1001" in url:
            return httpx.Response(200, json=sample_profile_payload)
        elif "/health" in url:
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404, json={"detail": "Not found"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    adapter = HttpHRMSAdapter(
        base_url="https://mock-hrms.internal",
        auth_token="dummy-auth-token",
        client=client,
    )

    # Profile
    profile = await adapter.get_profile("EMP-1001")
    assert profile.full_name == "Alex Rivera"

    # Attendance
    att = await adapter.get_attendance_today("EMP-1001")
    assert att.status == "Present"

    # Health
    assert await adapter.health_check() is True


@pytest.mark.asyncio
async def test_http_adapter_error_mapping() -> None:
    """Verify HttpHRMSAdapter maps upstream HTTP status codes to domain exceptions."""
    def error_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "401" in path:
            return httpx.Response(401, json={"detail": "Unauthorized token"})
        if "403" in path:
            return httpx.Response(403, json={"detail": "Forbidden scope"})
        if "404" in path:
            return httpx.Response(404, json={"detail": "Employee not found"})
        if "500" in path:
            return httpx.Response(500, json={"detail": "Internal server error"})
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(error_handler))
    adapter = HttpHRMSAdapter(base_url="https://mock-hrms.internal", client=client)

    # 401 Unauthorized
    with pytest.raises(AuthenticationError):
        await adapter.get_profile("EMP-401")

    # 403 Forbidden
    with pytest.raises(AuthorizationError):
        await adapter.get_profile("EMP-403")

    # 404 Not Found
    with pytest.raises(ResourceNotFoundError):
        await adapter.get_profile("EMP-404")

    # 500 Upstream Error
    with pytest.raises(HRMSAdapterError) as exc_info:
        await adapter.get_profile("EMP-500")
    assert "HRMS_UPSTREAM_ERROR" in exc_info.value.code


@pytest.mark.asyncio
async def test_http_adapter_timeout_handling() -> None:
    """Verify HttpHRMSAdapter handles timeouts with clean domain errors."""
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("Connection timed out")

    client = httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler))
    adapter = HttpHRMSAdapter(base_url="https://mock-hrms.internal", client=client)

    with pytest.raises(HRMSAdapterError) as exc_info:
        await adapter.get_profile("EMP-1001")
    assert exc_info.value.code == "HRMS_TIMEOUT"


def test_hrms_adapter_factory() -> None:
    """Verify create_hrms_adapter instantiates correct adapter based on type."""
    mock_adapter = create_hrms_adapter("mock")
    assert isinstance(mock_adapter, MockHRMSAdapter)

    http_adapter = create_hrms_adapter("http")
    assert isinstance(http_adapter, HttpHRMSAdapter)
