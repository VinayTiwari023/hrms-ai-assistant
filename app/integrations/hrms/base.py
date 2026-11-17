"""Abstract base contract for generic HRMS adapters."""

from abc import ABC, abstractmethod
from typing import Any

from app.schemas.hrms import (
    AttendanceToday,
    EmployeeProfile,
    HolidayItem,
    LeaveApplicationItem,
    LeaveBalance,
    MonthlyAttendanceSummary,
)


class BaseHRMSAdapter(ABC):
    """Abstract interface defining required HRMS integration methods.

    Concrete implementations must provide either mock sample data or live HTTP calls
    to external HRMS platforms, normalizing payloads into standard schema objects.
    """

    @abstractmethod
    async def get_profile(self, employee_id: str) -> EmployeeProfile:
        """Fetch employee profile details by ID."""
        pass

    @abstractmethod
    async def list_employees(self, department: str | None = None) -> list[EmployeeProfile]:
        """Fetch list of employees with optional department filter (restricted to HR/Admin)."""
        pass

    @abstractmethod
    async def get_attendance_today(self, employee_id: str) -> AttendanceToday:
        """Fetch daily attendance and punch times for today."""
        pass

    @abstractmethod
    async def get_monthly_attendance(
        self, employee_id: str, year: int, month: int
    ) -> MonthlyAttendanceSummary:
        """Fetch aggregated monthly attendance metrics for a specific month and year."""
        pass

    @abstractmethod
    async def get_leave_balance(self, employee_id: str, year: int | None = None) -> LeaveBalance:
        """Fetch current leave quotas and remaining balances."""
        pass

    @abstractmethod
    async def get_leave_applications(
        self, employee_id: str, status: str | None = None
    ) -> list[LeaveApplicationItem]:
        """Fetch past and pending leave applications."""
        pass

    @abstractmethod
    async def get_holiday_calendar(self, year: int) -> list[HolidayItem]:
        """Fetch company and public holiday schedule for the calendar year."""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Verify connectivity and readiness of the underlying HRMS service."""
        pass
