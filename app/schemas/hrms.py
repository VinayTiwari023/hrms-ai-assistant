"""Generic HRMS data schemas for employee, attendance, leave, and holiday information."""

from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, Field


class EmployeeProfile(BaseModel):
    """Generic employee profile information."""

    employee_id: str = Field(..., description="Unique employee identifier")
    first_name: str = Field(..., description="First name")
    last_name: str = Field(..., description="Last name")
    full_name: str = Field(..., description="Combined full name")
    email: str = Field(..., description="Corporate email address")
    department: str = Field(..., description="Assigned department")
    designation: str = Field(..., description="Job role or title")
    role: str = Field(default="employee", description="Access role (e.g. employee, hr_admin)")
    joining_date: str = Field(..., description="Date of joining (YYYY-MM-DD)")
    reporting_manager_id: str | None = Field(default=None, description="Manager employee ID")
    employment_status: Literal["active", "probation", "notice", "resigned"] = Field(
        default="active", description="Employment status"
    )


class AttendanceToday(BaseModel):
    """Daily attendance punch and status information."""

    employee_id: str = Field(..., description="Employee identifier")
    date: str = Field(..., description="Date (YYYY-MM-DD)")
    status: Literal["Present", "Absent", "Half-Day", "On Leave", "Holiday", "Weekend", "Not Marked"] = Field(
        ..., description="Attendance status for today"
    )
    check_in: str | None = Field(default=None, description="Check-in timestamp or time string (HH:MM)")
    check_out: str | None = Field(default=None, description="Check-out timestamp or time string (HH:MM)")
    work_minutes: int = Field(default=0, description="Total recorded work duration in minutes")
    is_late: bool = Field(default=False, description="Flag indicating late arrival")
    late_minutes: int = Field(default=0, description="Late arrival duration in minutes")


class MonthlyAttendanceSummary(BaseModel):
    """Aggregated monthly attendance statistics."""

    employee_id: str = Field(..., description="Employee identifier")
    year: int = Field(..., description="Calendar year")
    month: int = Field(..., description="Month number (1-12)")
    total_working_days: int = Field(..., description="Total official working days in month")
    present_days: float = Field(..., description="Days attended (including half-days)")
    absent_days: float = Field(..., description="Unexcused absence days")
    leave_days: float = Field(..., description="Approved leave days")
    holidays: int = Field(..., description="Official holiday count in month")
    late_count: int = Field(default=0, description="Number of times reported late")


class LeaveBalanceItem(BaseModel):
    """Leave balance details for a specific leave category."""

    leave_type: str = Field(..., description="Category name (e.g. Casual, Sick, Annual)")
    allocated: float = Field(..., description="Total allocated days for year")
    used: float = Field(..., description="Total days consumed")
    pending: float = Field(default=0.0, description="Days in pending applications")
    available: float = Field(..., description="Remaining balance available for booking")


class LeaveBalance(BaseModel):
    """Complete leave quota summary for an employee."""

    employee_id: str = Field(..., description="Employee identifier")
    year: int = Field(..., description="Calendar year")
    balances: list[LeaveBalanceItem] = Field(default_factory=list, description="Per-type breakdown")


class LeaveApplicationItem(BaseModel):
    """Individual leave request record."""

    application_id: str = Field(..., description="Unique application identifier")
    employee_id: str = Field(..., description="Employee identifier")
    leave_type: str = Field(..., description="Category name")
    start_date: str = Field(..., description="Start date (YYYY-MM-DD)")
    end_date: str = Field(..., description="End date (YYYY-MM-DD)")
    days: float = Field(..., description="Total requested days")
    reason: str = Field(..., description="Application reason")
    status: Literal["Pending", "Approved", "Rejected", "Cancelled"] = Field(..., description="Current status")
    applied_on: str = Field(..., description="Submission date (YYYY-MM-DD)")


class HolidayItem(BaseModel):
    """Public or corporate holiday record."""

    date: str = Field(..., description="Holiday date (YYYY-MM-DD)")
    name: str = Field(..., description="Holiday title")
    holiday_type: Literal["National", "Public", "Optional", "Company"] = Field(
        default="Public", description="Holiday category"
    )
    is_optional: bool = Field(default=False, description="Flag indicating optional/floating holiday")
