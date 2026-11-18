"""Deterministic unit tests for MockHRMSAdapter attendance accuracy and normalization."""

from datetime import date
import pytest

from app.core.exceptions import ResourceNotFoundError
from app.integrations.hrms.mock_adapter import MockHRMSAdapter
from app.schemas.hrms import AttendanceToday, EmployeeProfile


@pytest.fixture
def mock_adapter() -> MockHRMSAdapter:
    return MockHRMSAdapter()


@pytest.mark.asyncio
async def test_today_fixture_dates_normalized_to_current_date(mock_adapter: MockHRMSAdapter) -> None:
    """Verify that records in the mock dataset's 'today' section normalize to today's date."""
    att = await mock_adapter.get_attendance_today("EMP-1001")
    today_iso = date.today().isoformat()

    assert att.employee_id == "EMP-1001"
    assert att.date == today_iso
    assert att.status == "Present"
    assert att.check_in == "09:12"


@pytest.mark.asyncio
async def test_multiple_employees_receive_correctly_dated_records(mock_adapter: MockHRMSAdapter) -> None:
    """Verify multiple employees in the today fixture all receive the current date."""
    today_iso = date.today().isoformat()

    for emp_id, expected_status in [
        ("EMP-1001", "Present"),
        ("EMP-1002", "Present"),
        ("EMP-1003", "Present"),
        ("EMP-1004", "On Leave"),
    ]:
        att = await mock_adapter.get_attendance_today(emp_id)
        assert att.employee_id == emp_id
        assert att.date == today_iso
        assert att.status == expected_status


@pytest.mark.asyncio
async def test_missing_attendance_returns_not_marked_for_today(mock_adapter: MockHRMSAdapter) -> None:
    """Verify an employee without a today record receives a 'Not Marked' record with today's date."""
    # Register an employee profile with no entry in self.attendance_today
    mock_adapter.employees["EMP-9999"] = EmployeeProfile(
        employee_id="EMP-9999",
        first_name="Jane",
        last_name="Doe",
        full_name="Jane Doe",
        email="jane.doe@novatech-fictional.internal",
        department="Operations",
        designation="Analyst",
        role="employee",
        joining_date="2024-01-01",
        reporting_manager_id="EMP-1003",
        employment_status="active",
    )
    if "EMP-9999" in mock_adapter.attendance_today:
        del mock_adapter.attendance_today["EMP-9999"]

    att = await mock_adapter.get_attendance_today("EMP-9999")
    today_iso = date.today().isoformat()

    assert att.employee_id == "EMP-9999"
    assert att.date == today_iso
    assert att.status == "Not Marked"
    assert att.check_in is None
    assert att.work_minutes == 0


@pytest.mark.asyncio
async def test_historical_or_stale_record_normalized_on_retrieval(mock_adapter: MockHRMSAdapter) -> None:
    """Verify historical or stale dates in the adapter cannot silently be presented as today."""
    # Inject a stale record with a historical date
    stale_date = "2026-11-16"
    mock_adapter.attendance_today["EMP-1001"] = AttendanceToday(
        employee_id="EMP-1001",
        date=stale_date,
        status="Present",
        check_in="09:12",
        check_out=None,
        work_minutes=270,
        is_late=False,
        late_minutes=0,
    )

    # Retrieval must normalize to the actual current date
    att = await mock_adapter.get_attendance_today("EMP-1001")
    today_iso = date.today().isoformat()

    assert att.date != stale_date or today_iso == stale_date
    assert att.date == today_iso
    assert att.status == "Present"


@pytest.mark.asyncio
async def test_existing_attendance_fields_remain_intact(mock_adapter: MockHRMSAdapter) -> None:
    """Verify that date normalization does not alter or corrupt other sample fields."""
    att = await mock_adapter.get_attendance_today("EMP-1001")

    assert att.employee_id == "EMP-1001"
    assert att.status == "Present"
    assert att.check_in == "09:12"
    assert att.check_out is None
    assert att.work_minutes == 270
    assert att.is_late is False
    assert att.late_minutes == 0


@pytest.mark.asyncio
async def test_unknown_employee_raises_resource_not_found(mock_adapter: MockHRMSAdapter) -> None:
    """Verify querying attendance for a non-existent employee raises ResourceNotFoundError."""
    with pytest.raises(ResourceNotFoundError):
        await mock_adapter.get_attendance_today("EMP-UNKNOWN-999")
