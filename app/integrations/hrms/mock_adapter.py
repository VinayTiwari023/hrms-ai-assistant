"""In-memory mock HRMS adapter with realistic fictional sample data.

All data contained in this adapter is completely fictional for NovaTech Innovations and
designed for development, testing, and demonstration. No real employee records or
proprietary systems are used.
"""

from datetime import date
import json
import logging
from pathlib import Path
from typing import Any

from app.core.exceptions import ResourceNotFoundError
from app.integrations.hrms.base import BaseHRMSAdapter
from app.schemas.hrms import (
    AttendanceToday,
    EmployeeProfile,
    HolidayItem,
    LeaveApplicationItem,
    LeaveBalance,
    LeaveBalanceItem,
    MonthlyAttendanceSummary,
)

logger = logging.getLogger(__name__)

SAMPLE_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "sample_data"


class MockHRMSAdapter(BaseHRMSAdapter):
    """In-memory HRMS adapter for generic development and testing without external dependencies."""

    def __init__(self, sample_dir: Path | str | None = None) -> None:
        self.sample_dir = Path(sample_dir) if sample_dir else SAMPLE_DATA_DIR
        self.employees: dict[str, EmployeeProfile] = {}
        self.attendance_today: dict[str, AttendanceToday] = {}
        self.monthly_attendance: dict[str, MonthlyAttendanceSummary] = {}
        self.leave_balances: dict[str, LeaveBalance] = {}
        self.leave_applications: dict[str, list[LeaveApplicationItem]] = {}
        self.holidays: list[HolidayItem] = []
        self._load_sample_data()

    def _load_sample_data(self) -> None:
        """Load deterministic fictional sample dataset from JSON files or fall back to code defaults."""
        emp_file = self.sample_dir / "employees" / "employees.json"
        att_file = self.sample_dir / "employees" / "attendance.json"
        leave_file = self.sample_dir / "employees" / "leaves.json"
        holiday_file = self.sample_dir / "policies" / "holidays.json"

        # 1. Employees
        if emp_file.is_file():
            try:
                with open(emp_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data:
                        p = EmployeeProfile(**item)
                        self.employees[p.employee_id] = p
            except Exception as exc:
                logger.warning("Failed to load sample employees.json: %s", exc)

        # 2. Attendance
        if att_file.is_file():
            try:
                with open(att_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for emp_id, record in data.get("today", {}).items():
                        self.attendance_today[emp_id] = AttendanceToday(**record)
                    for emp_id, summary in data.get("monthly", {}).items():
                        self.monthly_attendance[emp_id] = MonthlyAttendanceSummary(**summary)
            except Exception as exc:
                logger.warning("Failed to load sample attendance.json: %s", exc)

        # 3. Leaves
        if leave_file.is_file():
            try:
                with open(leave_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for emp_id, bal in data.get("balances", {}).items():
                        self.leave_balances[emp_id] = LeaveBalance(**bal)
                    for emp_id, apps in data.get("applications", {}).items():
                        self.leave_applications[emp_id] = [LeaveApplicationItem(**a) for a in apps]
            except Exception as exc:
                logger.warning("Failed to load sample leaves.json: %s", exc)

        # 4. Holidays
        if holiday_file.is_file():
            try:
                with open(holiday_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.holidays = [HolidayItem(**h) for h in data]
            except Exception as exc:
                logger.warning("Failed to load sample holidays.json: %s", exc)

        # Guarantee fallback employee EMP-1001 if sample files were missing
        if "EMP-1001" not in self.employees:
            self.employees["EMP-1001"] = EmployeeProfile(
                employee_id="EMP-1001",
                first_name="Alex",
                last_name="Rivera",
                full_name="Alex Rivera",
                email="alex.rivera@novatech-fictional.internal",
                department="Engineering",
                designation="Senior Software Engineer",
                role="employee",
                joining_date="2023-03-15",
                reporting_manager_id="EMP-1003",
                employment_status="active",
            )
            self.attendance_today["EMP-1001"] = AttendanceToday(
                employee_id="EMP-1001",
                date=date.today().isoformat(),
                status="Present",
                check_in="09:12",
                check_out=None,
                work_minutes=240,
                is_late=False,
                late_minutes=0,
            )

    async def get_profile(self, employee_id: str) -> EmployeeProfile:
        emp = self.employees.get(employee_id.strip())
        if not emp:
            raise ResourceNotFoundError(f"Employee with ID '{employee_id}' not found in mock HRMS.")
        return emp

    async def list_employees(self, department: str | None = None) -> list[EmployeeProfile]:
        employees = list(self.employees.values())
        if department:
            return [e for e in employees if e.department.lower() == department.strip().lower()]
        return employees

    async def get_attendance_today(self, employee_id: str) -> AttendanceToday:
        if employee_id not in self.employees:
            raise ResourceNotFoundError(f"Employee with ID '{employee_id}' not found in mock HRMS.")
        return self.attendance_today.get(
            employee_id,
            AttendanceToday(
                employee_id=employee_id,
                date=date.today().isoformat(),
                status="Not Marked",
                check_in=None,
                check_out=None,
                work_minutes=0,
                is_late=False,
                late_minutes=0,
            ),
        )

    async def get_monthly_attendance(
        self, employee_id: str, year: int, month: int
    ) -> MonthlyAttendanceSummary:
        if employee_id not in self.employees:
            raise ResourceNotFoundError(f"Employee with ID '{employee_id}' not found in mock HRMS.")
        return self.monthly_attendance.get(
            employee_id,
            MonthlyAttendanceSummary(
                employee_id=employee_id,
                year=year,
                month=month,
                total_working_days=21,
                present_days=20.0,
                absent_days=0.0,
                leave_days=1.0,
                holidays=2,
                late_count=0,
            ),
        )

    async def get_leave_balance(self, employee_id: str, year: int | None = None) -> LeaveBalance:
        if employee_id not in self.employees:
            raise ResourceNotFoundError(f"Employee with ID '{employee_id}' not found in mock HRMS.")
        bal = self.leave_balances.get(employee_id)
        if not bal:
            return LeaveBalance(employee_id=employee_id, year=year or 2026, balances=[])
        return bal

    async def get_leave_applications(
        self, employee_id: str, status: str | None = None
    ) -> list[LeaveApplicationItem]:
        if employee_id not in self.employees:
            raise ResourceNotFoundError(f"Employee with ID '{employee_id}' not found in mock HRMS.")
        apps = self.leave_applications.get(employee_id, [])
        if status:
            return [a for a in apps if a.status.lower() == status.strip().lower()]
        return apps

    async def get_holiday_calendar(self, year: int) -> list[HolidayItem]:
        return self.holidays

    async def health_check(self) -> bool:
        return True
