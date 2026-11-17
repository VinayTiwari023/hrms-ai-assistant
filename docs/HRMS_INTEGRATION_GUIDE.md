# Enterprise HRMS Integration Guide

## 1. Overview & Architectural Boundaries

The **HRMS AI Assistant** provides an extensible, modular integration architecture designed to connect AI conversational workflows with enterprise Human Resource Management Systems.

> [!IMPORTANT]
> The repository ships with a **MockHRMSAdapter** containing purely fictional employee and policy records for NovaTech Innovations. This mock adapter is intended strictly for development, testing, and offline evaluation.
>
> **Production and staging environments strictly reject MockHRMSAdapter at startup.** A real enterprise HRMS adapter or configured `HttpHRMSAdapter` is required for live deployments.

---

## 2. Adapter Protocol Contract (`BaseHRMSAdapter`)

All HRMS integrations must implement the abstract base protocol defined in `app/integrations/hrms/base.py`:

```python
from abc import ABC, abstractmethod
from typing import Any, Optional
from app.schemas.hrms import (
    AttendanceRecord,
    EmployeeProfile,
    HolidayRecord,
    LeaveApplication,
    LeaveBalance,
)

class BaseHRMSAdapter(ABC):
    """Abstract interface defining required HRMS read-only operations."""

    @abstractmethod
    async def get_profile(self, employee_id: str) -> Optional[EmployeeProfile]:
        """Fetch employee biographical and organizational details."""
        ...

    @abstractmethod
    async def list_employees(self, department: Optional[str] = None) -> list[EmployeeProfile]:
        """List employee directory (restricted to HR and admin roles)."""
        ...

    @abstractmethod
    async def get_attendance_today(self, employee_id: str) -> Optional[AttendanceRecord]:
        """Fetch today's punch/presence record for the given employee."""
        ...

    @abstractmethod
    async def get_monthly_attendance(
        self, employee_id: str, year: int, month: int
    ) -> list[AttendanceRecord]:
        """Fetch monthly attendance records for the given employee."""
        ...

    @abstractmethod
    async def get_leave_balance(
        self, employee_id: str, year: Optional[int] = None
    ) -> list[LeaveBalance]:
        """Fetch remaining and accrued leave balances."""
        ...

    @abstractmethod
    async def get_leave_applications(
        self, employee_id: str, status: Optional[str] = None
    ) -> list[LeaveApplication]:
        """Fetch historical or pending leave requests."""
        ...

    @abstractmethod
    async def get_holiday_calendar(self, year: Optional[int] = None) -> list[HolidayRecord]:
        """Fetch official organizational holiday schedule."""
        ...

    @abstractmethod
    async def health_check(self) -> dict[str, Any]:
        """Verify upstream HRMS API availability and latency."""
        ...
```

---

## 3. Generic REST HTTP Adapter (`HttpHRMSAdapter`)

For external enterprise HRMS systems exposing REST APIs, the assistant provides `HttpHRMSAdapter` (`app/integrations/hrms/http_adapter.py`).

### 3.1 Configuration

Configure the following environment variables:

```bash
# Enable HTTP adapter
HRMS_ADAPTER_TYPE=http

# Base URL of your enterprise HRMS API (must be valid HTTP/HTTPS, non-placeholder)
HRMS_API_BASE_URL=https://hrms-internal.yourcompany.com/api/v1

# Request timeout in seconds
HRMS_API_TIMEOUT_SECONDS=10.0

# Keep mutations disabled for read-only safety
HRMS_MUTATIONS_ENABLED=false
```

### 3.2 Expected REST Endpoints

`HttpHRMSAdapter` expects the upstream HRMS API to expose standard RESTful endpoints returning JSON:

| Operation | Upstream HTTP Route | Expected Response Structure |
| :--- | :--- | :--- |
| `get_profile` | `GET /employees/{employee_id}` | JSON matching `EmployeeProfile` |
| `list_employees` | `GET /employees?department={dept}` | Array of `EmployeeProfile` objects |
| `get_attendance_today` | `GET /attendance/today?employee_id={id}` | JSON matching `AttendanceRecord` |
| `get_monthly_attendance` | `GET /attendance/monthly?employee_id={id}&year={y}&month={m}` | Array of `AttendanceRecord` objects |
| `get_leave_balance` | `GET /leaves/balance?employee_id={id}&year={y}` | Array of `LeaveBalance` objects |
| `get_leave_applications`| `GET /leaves/applications?employee_id={id}&status={s}` | Array of `LeaveApplication` objects |
| `get_holiday_calendar` | `GET /holidays?year={y}` | Array of `HolidayRecord` objects |
| `health_check` | `GET /health` | `{"status": "up"}` |

### 3.3 Upstream Authentication Injection

To inject enterprise authentication tokens or mTLS certificates into `HttpHRMSAdapter`, configure custom client headers in your application factory or dependency injection provider (`app/api/dependencies/services.py`):

```python
import httpx

# Example: Adding enterprise Bearer token or Service Account Header
client = httpx.AsyncClient(
    base_url=settings.hrms_api_base_url,
    headers={"Authorization": f"Bearer {enterprise_service_token}"},
    timeout=settings.hrms_api_timeout_seconds,
    verify=True,  # Enforce TLS certificate verification
)
adapter = HttpHRMSAdapter(base_url=settings.hrms_api_base_url, client=client)
```

---

## 4. Building a Custom Vendor Adapter

To integrate with proprietary vendor APIs (e.g. Workday, BambooHR, SAP SuccessFactors, ADP):

1. **Subclass `BaseHRMSAdapter`**:
   Create a new adapter class in `app/integrations/hrms/vendor_adapter.py`.
2. **Transform Vendor Responses into Standard Pydantic Models**:
   Ensure all returned data strictly conforms to `app.schemas.hrms` (`EmployeeProfile`, `LeaveBalance`, etc.).
3. **Register in Factory**:
   Add the adapter key to `get_hrms_adapter()` in `app/integrations/hrms/factory.py`:
   ```python
   if adapter_type == "workday":
       return WorkdayHRMSAdapter(...)
   ```

---

## 5. Security Invariants for HRMS Integrations

### 5.1 Strict Identity Derivation
Self-service tools (`get_my_profile`, `get_my_leave_balance`, `get_my_attendance_today`) derive the `employee_id` strictly from verified JWT claims (`current_user.employee_id`). The adapter must never accept client-overridden employee IDs for personal queries.

### 5.2 Server-Side Role Gating
Privileged tools (`get_employee_profile`, `list_employees`, `get_employee_leave_balance`) must only be accessible to `hr` and `admin` roles. Standard employees attempting to query other employees will be rejected server-side with `403 Forbidden`.

### 5.3 Read-Only Mutation Gate
All mutation endpoints (leave booking, attendance correction, profile edits) must remain gated behind `HRMS_MUTATIONS_ENABLED=false` until explicit audit trails and approval workflows are established.
