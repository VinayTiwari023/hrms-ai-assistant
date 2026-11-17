"""Unit tests for HRMS tool registry, role-based visibility, and tool execution."""

import pytest

from app.integrations.hrms.mock_adapter import MockHRMSAdapter
from app.schemas.auth import AuthenticatedPrincipal
from app.services.hrms.tools import build_default_tool_registry
from app.services.hrms.service import HRMSToolService


@pytest.fixture
def hrms_tool_service():
    """Create HRMSToolService backed by MockHRMSAdapter and default tool registry."""
    adapter = MockHRMSAdapter()
    registry = build_default_tool_registry()
    return HRMSToolService(adapter=adapter, registry=registry)


@pytest.fixture
def employee_principal():
    """Standard employee authenticated principal."""
    return AuthenticatedPrincipal(
        user_id="user-emp-1",
        email="alex.rivera@novatech-fictional.internal",
        employee_id="EMP-1001",
        role="employee",
    )


@pytest.fixture
def hr_principal():
    """HR specialist authenticated principal."""
    return AuthenticatedPrincipal(
        user_id="user-hr-1",
        email="sarah.chen@novatech-fictional.internal",
        employee_id="EMP-1002",
        role="hr",
    )


@pytest.fixture
def admin_principal():
    """Administrator authenticated principal."""
    return AuthenticatedPrincipal(
        user_id="user-admin-1",
        email="marcus.vance@novatech-fictional.internal",
        employee_id="EMP-1003",
        role="admin",
    )


def test_tool_registry_registration_and_role_filtering() -> None:
    """Verify tool registry filters tool availability by caller role."""
    registry = build_default_tool_registry()

    # Total tools registered
    all_tools = registry.list_tools()
    assert len(all_tools) >= 11

    # Employee role tool listing
    emp_tools = registry.list_tools(role="employee")
    emp_tool_names = {t.name for t in emp_tools}
    assert "get_my_profile" in emp_tool_names
    assert "get_my_attendance_today" in emp_tool_names
    assert "get_my_leave_balance" in emp_tool_names
    assert "get_holiday_calendar" in emp_tool_names
    assert "search_company_knowledge" in emp_tool_names
    # Privileged HR tools must NOT be exposed to standard employees
    assert "get_employee_profile" not in emp_tool_names
    assert "list_employees" not in emp_tool_names
    assert "get_employee_attendance" not in emp_tool_names
    assert "get_employee_leave_balance" not in emp_tool_names

    # HR role tool listing
    hr_tools = registry.list_tools(role="hr")
    hr_tool_names = {t.name for t in hr_tools}
    assert "get_my_profile" in hr_tool_names
    assert "get_employee_profile" in hr_tool_names
    assert "list_employees" in hr_tool_names

    # Ollama schema conversion
    ollama_tools = registry.to_ollama_tools(role="employee")
    assert isinstance(ollama_tools, list)
    assert all(t.get("type") == "function" for t in ollama_tools)
    assert all("name" in t.get("function", {}) for t in ollama_tools)


@pytest.mark.asyncio
async def test_employee_tools_identity_binding(hrms_tool_service, employee_principal) -> None:
    """Verify personal tools strictly bind to principal.employee_id and ignore client spoofing."""
    # Even if model or client supplies an arbitrary employee_id, get_my_profile ignores it
    spoofed_args = {"employee_id": "EMP-1002"}
    res = await hrms_tool_service.execute_tool("get_my_profile", spoofed_args, employee_principal)
    assert res.success is True
    # Returned profile is strictly Alex Rivera (EMP-1001), NOT Sarah Chen (EMP-1002)
    assert res.data["employee_id"] == "EMP-1001"
    assert res.data["full_name"] == "Alex Rivera"

    # Attendance today
    att_res = await hrms_tool_service.execute_tool("get_my_attendance_today", {}, employee_principal)
    assert att_res.success is True
    assert att_res.data["employee_id"] == "EMP-1001"

    # Monthly attendance
    monthly_res = await hrms_tool_service.execute_tool(
        "get_my_monthly_attendance", {"year": 2026, "month": 11}, employee_principal
    )
    assert monthly_res.success is True
    assert monthly_res.data["employee_id"] == "EMP-1001"

    # Leave balance
    bal_res = await hrms_tool_service.execute_tool("get_my_leave_balance", {}, employee_principal)
    assert bal_res.success is True
    assert bal_res.data["employee_id"] == "EMP-1001"

    # Leave applications
    apps_res = await hrms_tool_service.execute_tool(
        "get_my_leave_applications", {"status": "Approved"}, employee_principal
    )
    assert apps_res.success is True
    assert isinstance(apps_res.data, list)

    # Holidays
    hol_res = await hrms_tool_service.execute_tool("get_holiday_calendar", {}, employee_principal)
    assert hol_res.success is True
    assert len(hol_res.data) >= 5

    # Knowledge search placeholder
    know_res = await hrms_tool_service.execute_tool(
        "search_company_knowledge", {"query": "leave policy"}, employee_principal
    )
    assert know_res.success is True
    assert "placeholder" in know_res.data["status"]


@pytest.mark.asyncio
async def test_role_authorization_and_cross_employee_access(
    hrms_tool_service, employee_principal, hr_principal, admin_principal
) -> None:
    """Verify standard employees cannot invoke HR tools, while HR and Admin can."""
    # 1. Standard employee calling get_employee_profile -> DENIED
    res_emp = await hrms_tool_service.execute_tool(
        "get_employee_profile", {"employee_id": "EMP-1002"}, employee_principal
    )
    assert res_emp.success is False
    assert "Access denied" in res_emp.error

    # 2. Standard employee calling list_employees -> DENIED
    res_list_emp = await hrms_tool_service.execute_tool("list_employees", {}, employee_principal)
    assert res_list_emp.success is False
    assert "Access denied" in res_list_emp.error

    # 3. HR caller calling get_employee_profile for EMP-1001 -> ALLOWED
    res_hr = await hrms_tool_service.execute_tool(
        "get_employee_profile", {"employee_id": "EMP-1001"}, hr_principal
    )
    assert res_hr.success is True
    assert res_hr.data["full_name"] == "Alex Rivera"

    # 4. HR caller calling list_employees -> ALLOWED
    res_hr_list = await hrms_tool_service.execute_tool("list_employees", {}, hr_principal)
    assert res_hr_list.success is True
    assert len(res_hr_list.data) >= 3

    # 5. Admin caller calling get_employee_attendance -> ALLOWED
    res_admin = await hrms_tool_service.execute_tool(
        "get_employee_attendance", {"employee_id": "EMP-1001"}, admin_principal
    )
    assert res_admin.success is True
    assert res_admin.data["status"] == "Present"


@pytest.mark.asyncio
async def test_tool_parameter_validation(hrms_tool_service, hr_principal) -> None:
    """Verify tool parameter schema validation rejects missing required arguments."""
    # get_employee_profile requires 'employee_id'
    res = await hrms_tool_service.execute_tool("get_employee_profile", {}, hr_principal)
    assert res.success is False
    assert "Missing required parameter" in res.error


@pytest.mark.asyncio
async def test_mutation_tools_disabled(hrms_tool_service, employee_principal) -> None:
    """Verify mutation tools are strictly blocked by configuration."""
    res = await hrms_tool_service.execute_tool(
        "apply_for_leave",
        {
            "leave_type": "Annual Leave",
            "start_date": "2026-12-01",
            "end_date": "2026-12-02",
            "reason": "Holiday trip",
        },
        employee_principal,
    )
    assert res.success is False
    assert "disabled by configuration" in res.error


@pytest.mark.asyncio
async def test_multiple_tool_execution_and_timings(hrms_tool_service, employee_principal) -> None:
    """Verify execute_tools executes multiple calls in one turn and records valid timings."""
    calls = [
        {"name": "get_my_profile", "arguments": {}},
        {"name": "get_my_attendance_today", "arguments": {}},
        {"name": "get_holiday_calendar", "arguments": {"year": 2026}},
    ]

    results = await hrms_tool_service.execute_tools(calls, employee_principal)
    assert len(results) == 3
    assert all(r.success is True for r in results)
    assert results[0].tool_name == "get_my_profile"
    assert results[1].tool_name == "get_my_attendance_today"
    assert results[2].tool_name == "get_holiday_calendar"

    # All execution timings must be recorded non-negative floats
    for r in results:
        assert isinstance(r.execution_time_ms, float)
        assert r.execution_time_ms >= 0.0
