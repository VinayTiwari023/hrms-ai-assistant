"""Concrete HRMS tool handlers implementing role-aware data retrieval."""

from datetime import datetime, timezone
import logging
from typing import Any

from app.core.exceptions import AuthorizationError, HRMSMutationBlockedError
from app.integrations.hrms.base import BaseHRMSAdapter
from app.schemas.auth import AuthenticatedPrincipal
from app.services.hrms.registry import (
    ALL_ROLES,
    HR_AND_ADMIN_ROLES,
    ToolDefinition,
    ToolRegistry,
)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------------------
# Employee Self-Service Tools (Strictly bound to principal.employee_id)
# ------------------------------------------------------------------------------


async def handle_get_my_profile(
    adapter: BaseHRMSAdapter,
    principal: AuthenticatedPrincipal,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Retrieve personal employee profile. Ignores any client-supplied employee ID."""
    profile = await adapter.get_profile(principal.employee_id)
    return profile.model_dump()


async def handle_get_my_attendance_today(
    adapter: BaseHRMSAdapter,
    principal: AuthenticatedPrincipal,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Retrieve today's attendance for the authenticated employee."""
    attendance = await adapter.get_attendance_today(principal.employee_id)
    return attendance.model_dump()


async def handle_get_my_monthly_attendance(
    adapter: BaseHRMSAdapter,
    principal: AuthenticatedPrincipal,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Retrieve monthly attendance summary for the authenticated employee."""
    now = datetime.now(timezone.utc)
    year = int(args.get("year") or now.year)
    month = int(args.get("month") or now.month)
    summary = await adapter.get_monthly_attendance(principal.employee_id, year, month)
    return summary.model_dump()


async def handle_get_my_leave_balance(
    adapter: BaseHRMSAdapter,
    principal: AuthenticatedPrincipal,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Retrieve current leave quota balances for the authenticated employee."""
    year = int(args["year"]) if "year" in args and args["year"] is not None else None
    balance = await adapter.get_leave_balance(principal.employee_id, year=year)
    return balance.model_dump()


async def handle_get_my_leave_applications(
    adapter: BaseHRMSAdapter,
    principal: AuthenticatedPrincipal,
    args: dict[str, Any],
) -> list[dict[str, Any]]:
    """Retrieve past and pending leave applications for the authenticated employee."""
    status_filter = str(args.get("status") or "").strip() or None
    apps = await adapter.get_leave_applications(principal.employee_id, status=status_filter)
    return [a.model_dump() for a in apps]


async def handle_get_holiday_calendar(
    adapter: BaseHRMSAdapter,
    principal: AuthenticatedPrincipal,
    args: dict[str, Any],
) -> list[dict[str, Any]]:
    """Retrieve corporate holiday calendar for the year."""
    now = datetime.now(timezone.utc)
    year = int(args.get("year") or now.year)
    holidays = await adapter.get_holiday_calendar(year)
    return [h.model_dump() for h in holidays]


async def handle_search_company_knowledge(
    adapter: BaseHRMSAdapter,
    principal: AuthenticatedPrincipal,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Search official company policies and guidelines using semantic RAG retrieval."""
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("Missing required 'query' parameter.")

    from app.api.dependencies.knowledge import get_retrieval_service
    from app.services.knowledge.citation_service import STANDARD_RAG_FALLBACK

    service = get_retrieval_service()
    category = str(args.get("category") or "").strip() or None
    top_k = int(args["top_k"]) if "top_k" in args and args["top_k"] is not None else None

    try:
        result = await service.search(
            query=query,
            principal=principal,
            top_k=top_k,
            category=category,
        )
        return {
            "query": query,
            "status": "placeholder_resolved" if result.found else "placeholder",
            "found": result.found,
            "message": result.answer_context if result.found else result.fallback_message,
            "fallback": not result.found,
            "citations": [c.model_dump() for c in result.citations],
            "documents": [c.model_dump() for c in result.chunks],
        }
    except Exception as exc:
        logger.error("Error executing search_company_knowledge: %s", exc)
        return {
            "query": query,
            "status": "placeholder",
            "found": False,
            "message": STANDARD_RAG_FALLBACK,
            "fallback": True,
            "error": str(exc),
            "citations": [],
            "documents": [],
        }


# ------------------------------------------------------------------------------
# HR & Administrative Privileged Tools (Cross-employee inspection)
# ------------------------------------------------------------------------------


async def handle_get_employee_profile(
    adapter: BaseHRMSAdapter,
    principal: AuthenticatedPrincipal,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Look up an employee profile by ID. Restricted to HR and Admin roles."""
    if not principal.is_hr_or_admin:
        raise AuthorizationError("Only HR or Admin personnel may inspect other employee profiles.")

    target_id = str(args.get("employee_id") or "").strip()
    if not target_id:
        raise ValueError("Missing required 'employee_id' parameter.")

    profile = await adapter.get_profile(target_id)
    return profile.model_dump()


async def handle_list_employees(
    adapter: BaseHRMSAdapter,
    principal: AuthenticatedPrincipal,
    args: dict[str, Any],
) -> list[dict[str, Any]]:
    """List directory of employees. Restricted to HR and Admin roles."""
    if not principal.is_hr_or_admin:
        raise AuthorizationError("Only HR or Admin personnel may list the employee directory.")

    dept = str(args.get("department") or "").strip() or None
    employees = await adapter.list_employees(department=dept)
    return [e.model_dump() for e in employees]


async def handle_get_employee_attendance(
    adapter: BaseHRMSAdapter,
    principal: AuthenticatedPrincipal,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Look up attendance for any employee. Restricted to HR and Admin roles."""
    if not principal.is_hr_or_admin:
        raise AuthorizationError("Only HR or Admin personnel may view other employee attendance records.")

    target_id = str(args.get("employee_id") or "").strip()
    if not target_id:
        raise ValueError("Missing required 'employee_id' parameter.")

    attendance = await adapter.get_attendance_today(target_id)
    return attendance.model_dump()


async def handle_get_employee_leave_balance(
    adapter: BaseHRMSAdapter,
    principal: AuthenticatedPrincipal,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Look up leave balance for any employee. Restricted to HR and Admin roles."""
    if not principal.is_hr_or_admin:
        raise AuthorizationError("Only HR or Admin personnel may view other employee leave balances.")

    target_id = str(args.get("employee_id") or "").strip()
    if not target_id:
        raise ValueError("Missing required 'employee_id' parameter.")

    year = int(args["year"]) if "year" in args and args["year"] is not None else None
    balance = await adapter.get_leave_balance(target_id, year=year)
    return balance.model_dump()


# ------------------------------------------------------------------------------
# Mutation Tool (Disabled by default)
# ------------------------------------------------------------------------------


async def handle_apply_leave(
    adapter: BaseHRMSAdapter,
    principal: AuthenticatedPrincipal,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Disabled mutation tool example demonstrating mutation gating."""
    raise HRMSMutationBlockedError("apply_leave")


# ------------------------------------------------------------------------------
# Tool Registry Factory & Registration
# ------------------------------------------------------------------------------


def build_default_tool_registry() -> ToolRegistry:
    """Build and populate the default HRMS tool registry with defined schemas and authorization."""
    registry = ToolRegistry()

    # 1. get_my_profile
    registry.register(
        ToolDefinition(
            name="get_my_profile",
            description="Retrieve the authenticated employee's personal profile information, including department, designation, and manager.",
            parameters={"type": "object", "properties": {}},
            allowed_roles=ALL_ROLES,
            handler=handle_get_my_profile,
            is_mutation=False,
        )
    )

    # 2. get_my_attendance_today
    registry.register(
        ToolDefinition(
            name="get_my_attendance_today",
            description="Retrieve the authenticated employee's daily attendance punch status, check-in time, and recorded work minutes for today.",
            parameters={"type": "object", "properties": {}},
            allowed_roles=ALL_ROLES,
            handler=handle_get_my_attendance_today,
            is_mutation=False,
        )
    )

    # 3. get_my_monthly_attendance
    registry.register(
        ToolDefinition(
            name="get_my_monthly_attendance",
            description="Retrieve the authenticated employee's aggregated monthly attendance summary, present days, absences, and late arrivals for a specific month and year.",
            parameters={
                "type": "object",
                "properties": {
                    "year": {"type": "integer", "description": "Calendar year (e.g. 2026)"},
                    "month": {"type": "integer", "description": "Month number (1-12)"},
                },
            },
            allowed_roles=ALL_ROLES,
            handler=handle_get_my_monthly_attendance,
            is_mutation=False,
        )
    )

    # 4. get_my_leave_balance
    registry.register(
        ToolDefinition(
            name="get_my_leave_balance",
            description="Retrieve the authenticated employee's current leave balance breakdown across annual, sick, and casual leave categories.",
            parameters={
                "type": "object",
                "properties": {
                    "year": {"type": "integer", "description": "Calendar year (default current year)"},
                },
            },
            allowed_roles=ALL_ROLES,
            handler=handle_get_my_leave_balance,
            is_mutation=False,
        )
    )

    # 5. get_my_leave_applications
    registry.register(
        ToolDefinition(
            name="get_my_leave_applications",
            description="Retrieve the authenticated employee's past and pending leave applications with submission dates, leave types, and approval status.",
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["Pending", "Approved", "Rejected", "Cancelled"],
                        "description": "Optional status filter",
                    },
                },
            },
            allowed_roles=ALL_ROLES,
            handler=handle_get_my_leave_applications,
            is_mutation=False,
        )
    )

    # 6. get_holiday_calendar
    registry.register(
        ToolDefinition(
            name="get_holiday_calendar",
            description="Retrieve the official company and public holiday schedule for the calendar year.",
            parameters={
                "type": "object",
                "properties": {
                    "year": {"type": "integer", "description": "Calendar year (e.g. 2026)"},
                },
            },
            allowed_roles=ALL_ROLES,
            handler=handle_get_holiday_calendar,
            is_mutation=False,
        )
    )

    # 7. search_company_knowledge
    registry.register(
        ToolDefinition(
            name="search_company_knowledge",
            description="Search official company policies, employee handbook, benefits, working hours, and workplace guidelines.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Policy topic or query keyword"},
                },
                "required": ["query"],
            },
            allowed_roles=ALL_ROLES,
            handler=handle_search_company_knowledge,
            is_mutation=False,
        )
    )

    # 8. Privileged: get_employee_profile
    registry.register(
        ToolDefinition(
            name="get_employee_profile",
            description="Look up any employee's profile by their employee ID. Restricted strictly to HR and Admin roles.",
            parameters={
                "type": "object",
                "properties": {
                    "employee_id": {"type": "string", "description": "Target employee identifier (e.g. EMP-1001)"},
                },
                "required": ["employee_id"],
            },
            allowed_roles=HR_AND_ADMIN_ROLES,
            handler=handle_get_employee_profile,
            is_mutation=False,
        )
    )

    # 9. Privileged: list_employees
    registry.register(
        ToolDefinition(
            name="list_employees",
            description="List directory of employees across the organization, optionally filtered by department. Restricted to HR and Admin roles.",
            parameters={
                "type": "object",
                "properties": {
                    "department": {"type": "string", "description": "Optional department name filter"},
                },
            },
            allowed_roles=HR_AND_ADMIN_ROLES,
            handler=handle_list_employees,
            is_mutation=False,
        )
    )

    # 10. Privileged: get_employee_attendance
    registry.register(
        ToolDefinition(
            name="get_employee_attendance",
            description="Look up daily attendance status for any employee by employee ID. Restricted to HR and Admin roles.",
            parameters={
                "type": "object",
                "properties": {
                    "employee_id": {"type": "string", "description": "Target employee identifier"},
                },
                "required": ["employee_id"],
            },
            allowed_roles=HR_AND_ADMIN_ROLES,
            handler=handle_get_employee_attendance,
            is_mutation=False,
        )
    )

    # 11. Privileged: get_employee_leave_balance
    registry.register(
        ToolDefinition(
            name="get_employee_leave_balance",
            description="Look up leave quotas and available balance for any employee by employee ID. Restricted to HR and Admin roles.",
            parameters={
                "type": "object",
                "properties": {
                    "employee_id": {"type": "string", "description": "Target employee identifier"},
                    "year": {"type": "integer", "description": "Calendar year"},
                },
                "required": ["employee_id"],
            },
            allowed_roles=HR_AND_ADMIN_ROLES,
            handler=handle_get_employee_leave_balance,
            is_mutation=False,
        )
    )

    # 12. Mutation Gated: apply_for_leave
    registry.register(
        ToolDefinition(
            name="apply_for_leave",
            description="Submit a leave application for approval. Disabled by configuration in read-only mode.",
            parameters={
                "type": "object",
                "properties": {
                    "leave_type": {"type": "string", "description": "Category name"},
                    "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                    "reason": {"type": "string", "description": "Reason"},
                },
                "required": ["leave_type", "start_date", "end_date"],
            },
            allowed_roles=ALL_ROLES,
            handler=handle_apply_leave,
            is_mutation=True,
        )
    )

    return registry
