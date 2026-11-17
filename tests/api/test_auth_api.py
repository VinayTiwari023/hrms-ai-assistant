"""API integration tests for authentication, role enforcement, rate limiting, and security hardening."""

from datetime import timedelta
import pytest
import httpx
from fastapi import APIRouter, Depends, status

from app.api.dependencies.auth import (
    enforce_employee_isolation,
    get_current_user,
    get_hrms_adapter,
    get_user_repository,
    require_admin,
    require_employee,
    require_hr,
)
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    create_access_token,
    hash_password,
    token_blacklist,
    verify_password,
)
from app.integrations.hrms.mock_adapter import MockHRMSAdapter
from app.main import create_app
from app.repositories.user_repo import UserRepository
from app.schemas.auth import AuthenticatedPrincipal


@pytest.fixture
def test_app():
    """Create test application configured with isolated in-memory repositories and rate limiter."""
    app = create_app()
    test_user_repo = UserRepository(db_url="sqlite:///:memory:")
    test_hrms_adapter = MockHRMSAdapter()

    # Clear rate limiter and token revocation blacklist between tests
    login_rate_limiter.clear()
    token_blacklist.clear()

    # Override dependencies
    app.dependency_overrides[get_user_repository] = lambda: test_user_repo
    app.dependency_overrides[get_hrms_adapter] = lambda: test_hrms_adapter

    # Add protected inspection routes for role and isolation verification
    test_router = APIRouter(prefix="/api/v1/test")

    @test_router.get("/employee-zone")
    def employee_zone(user: AuthenticatedPrincipal = Depends(require_employee)):
        return {"message": "employee_access_granted", "user": user.email}

    @test_router.get("/hr-zone")
    def hr_zone(user: AuthenticatedPrincipal = Depends(require_hr)):
        return {"message": "hr_access_granted", "user": user.email}

    @test_router.get("/admin-zone")
    def admin_zone(user: AuthenticatedPrincipal = Depends(require_admin)):
        return {"message": "admin_access_granted", "user": user.email}

    @test_router.get("/records/{target_employee_id}")
    def view_records(
        target_employee_id: str,
        current_user: AuthenticatedPrincipal = Depends(get_current_user),
    ):
        enforce_employee_isolation(target_employee_id, current_user)
        return {"message": "record_view_allowed", "target": target_employee_id}

    app.include_router(test_router)
    return app


@pytest.mark.asyncio
async def test_user_registration_employee_role_enforcement(test_app) -> None:
    """Verify public registration restricts role to 'employee' and validates HRMS identity."""
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Successful Registration (EMP-1001 exists in MockHRMSAdapter)
        reg_payload = {
            "email": "alex.rivera@novatech-fictional.internal",
            "password": "Password123!",
            "full_name": "Alex Rivera",
            "employee_id": "EMP-1001",
        }
        res = await client.post("/api/v1/auth/register", json=reg_payload)
        assert res.status_code == status.HTTP_201_CREATED
        data = res.json()
        assert data["email"] == "alex.rivera@novatech-fictional.internal"
        assert data["employee_id"] == "EMP-1001"
        assert data["role"] == "employee"  # Must strictly be employee
        assert "hashed_password" not in data

        # 2. Employee Identity Spoofing Protection (non-existent employee ID rejected)
        spoof_payload = {
            "email": "attacker@example.com",
            "password": "Password123!",
            "full_name": "Attacker",
            "employee_id": "EMP-NONEXISTENT-9999",
        }
        res_spoof = await client.post("/api/v1/auth/register", json=spoof_payload)
        assert res_spoof.status_code == status.HTTP_400_BAD_REQUEST
        assert "not recognized in the HRMS directory" in res_spoof.json()["detail"]

        # 3. Duplicate Email Rejection
        dup_email_payload = {
            "email": "alex.rivera@novatech-fictional.internal",
            "password": "AnotherPassword123!",
            "full_name": "Another User",
            "employee_id": "EMP-1002",
        }
        res_dup_email = await client.post("/api/v1/auth/register", json=dup_email_payload)
        assert res_dup_email.status_code == status.HTTP_400_BAD_REQUEST
        assert "already registered" in res_dup_email.json()["detail"]

        # 4. Duplicate Employee ID Rejection
        dup_emp_payload = {
            "email": "new.user@novatech-fictional.internal",
            "password": "Password123!",
            "full_name": "New User",
            "employee_id": "EMP-1001",
        }
        res_dup_emp = await client.post("/api/v1/auth/register", json=dup_emp_payload)
        assert res_dup_emp.status_code == status.HTTP_400_BAD_REQUEST
        assert "already registered" in res_dup_emp.json()["detail"]


@pytest.mark.asyncio
async def test_admin_user_provisioning_and_role_escalation_defense(test_app) -> None:
    """Verify privileged HR/admin roles can only be provisioned by authenticated administrators."""
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Register a standard employee
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": "emp.user@novatech-fictional.internal",
                "password": "Password123!",
                "full_name": "Employee User",
                "employee_id": "EMP-1001",
            },
        )
        login_emp = await client.post(
            "/api/v1/auth/login",
            json={"email": "emp.user@novatech-fictional.internal", "password": "Password123!"},
        )
        emp_token = login_emp.json()["access_token"]

        # 2. Standard employee attempts to call /provision to create an admin -> 403 Forbidden
        provision_payload = {
            "email": "privileged.admin@novatech-fictional.internal",
            "password": "AdminPassword123!",
            "full_name": "Privileged Admin",
            "employee_id": "EMP-9001",
            "role": "admin",
        }
        res_unauthorized = await client.post(
            "/api/v1/auth/provision",
            json=provision_payload,
            headers={"Authorization": f"Bearer {emp_token}"},
        )
        assert res_unauthorized.status_code == status.HTTP_403_FORBIDDEN

        # 3. Create a legitimate admin token directly in test setup
        user_repo = test_app.dependency_overrides[get_user_repository]()
        admin_user = user_repo.create_user(
            email="root.admin@novatech-fictional.internal",
            hashed_password=hash_password("AdminRootPass123!"),
            full_name="Root Admin",
            employee_id="ADM-001",
            role="admin",
        )
        admin_token = create_access_token(
            subject=admin_user["id"],
            email=admin_user["email"],
            role="admin",
            employee_id="ADM-001",
        )

        # 4. Legitimate admin provisions an HR specialist account -> 201 Created
        hr_provision_payload = {
            "email": "hr.specialist@novatech-fictional.internal",
            "password": "HrPassword123!",
            "full_name": "HR Specialist",
            "employee_id": "HR-001",
            "role": "hr",
        }
        res_hr_provision = await client.post(
            "/api/v1/auth/provision",
            json=hr_provision_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res_hr_provision.status_code == status.HTTP_201_CREATED
        hr_data = res_hr_provision.json()
        assert hr_data["role"] == "hr"
        assert hr_data["email"] == "hr.specialist@novatech-fictional.internal"


@pytest.mark.asyncio
async def test_login_rate_limiting_and_enumeration_prevention(test_app) -> None:
    """Verify rate-limiting lockouts and identical error responses preventing user enumeration."""
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Register a valid employee
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": "ratelimit.test@novatech-fictional.internal",
                "password": "CorrectPassword123!",
                "full_name": "Rate Limit Test",
                "employee_id": "EMP-1001",
            },
        )

        # 1. Verify non-existent email and wrong password return identical error
        res_wrong_email = await client.post(
            "/api/v1/auth/login",
            json={"email": "nonexistent@novatech-fictional.internal", "password": "AnyPassword123!"},
        )
        res_wrong_pwd = await client.post(
            "/api/v1/auth/login",
            json={"email": "ratelimit.test@novatech-fictional.internal", "password": "WrongPassword!"},
        )
        assert res_wrong_email.status_code == status.HTTP_401_UNAUTHORIZED
        assert res_wrong_pwd.status_code == status.HTTP_401_UNAUTHORIZED
        assert res_wrong_email.json()["detail"] == res_wrong_pwd.json()["detail"] == "Invalid email or password"

        # 2. Trigger rate limiting lockout (default threshold: 5 attempts)
        target_email = "ratelimit.target@novatech-fictional.internal"
        for i in range(4):
            fail_res = await client.post(
                "/api/v1/auth/login",
                json={"email": target_email, "password": f"bad_pwd_{i}"},
            )
            assert fail_res.status_code == status.HTTP_401_UNAUTHORIZED

        # 5th attempt: Reaches threshold and locks
        fifth_res = await client.post(
            "/api/v1/auth/login",
            json={"email": target_email, "password": "bad_pwd_5"},
        )
        assert fifth_res.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Retry-After" in fifth_res.headers

        # 6th attempt: Immediately blocked with 429 Too Many Requests
        lockout_res = await client.post(
            "/api/v1/auth/login",
            json={"email": target_email, "password": "any_password"},
        )
        assert lockout_res.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert "temporarily locked" in lockout_res.json()["detail"]
        assert "Retry-After" in lockout_res.headers


@pytest.mark.asyncio
async def test_password_hash_auto_migration_on_login(test_app) -> None:
    """Verify legacy 100k password hashes are automatically re-hashed to 600k upon successful login."""
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        user_repo = test_app.dependency_overrides[get_user_repository]()

        # Insert user directly with legacy 100,000 iteration hash
        legacy_hash = hash_password("MigrationPassword123!", iterations=100_000)
        assert "100000" in legacy_hash
        user = user_repo.create_user(
            email="migrate.me@novatech-fictional.internal",
            hashed_password=legacy_hash,
            full_name="Migration User",
            employee_id="EMP-1001",
            role="employee",
        )

        # Login with correct password
        login_res = await client.post(
            "/api/v1/auth/login",
            json={"email": "migrate.me@novatech-fictional.internal", "password": "MigrationPassword123!"},
        )
        assert login_res.status_code == status.HTTP_200_OK

        # Verify database record was automatically updated to 600,000 iterations
        updated_user = user_repo.get_by_id(user["id"])
        new_hash = updated_user["hashed_password"]
        assert new_hash != legacy_hash
        assert "600000" in new_hash
        assert verify_password("MigrationPassword123!", new_hash) is True


@pytest.mark.asyncio
async def test_logout_and_token_revocation(test_app) -> None:
    """Verify logging out revokes token and blocks subsequent requests."""
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Register and login
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": "logout.test@novatech-fictional.internal",
                "password": "Password123!",
                "full_name": "Logout Tester",
                "employee_id": "EMP-1001",
            },
        )
        login_res = await client.post(
            "/api/v1/auth/login",
            json={"email": "logout.test@novatech-fictional.internal", "password": "Password123!"},
        )
        token = login_res.json()["access_token"]

        # Token is valid before logout
        me_res = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me_res.status_code == status.HTTP_200_OK

        # Call logout endpoint
        logout_res = await client.post(
            "/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}
        )
        assert logout_res.status_code == status.HTTP_200_OK
        assert "revoked" in logout_res.json()["message"]

        # Subsequent attempt to use revoked token is rejected with 401
        revoked_res = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert revoked_res.status_code == status.HTTP_401_UNAUTHORIZED
        assert "revoked" in revoked_res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_server_side_role_enforcement(test_app) -> None:
    """Verify role-based authorization: employee cannot access HR/admin routes."""
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        user_repo = test_app.dependency_overrides[get_user_repository]()

        # Seed employee, hr, and admin accounts
        for role, emp_id in [("employee", "EMP-1001"), ("hr", "EMP-1002"), ("admin", "EMP-1003")]:
            user_repo.create_user(
                email=f"{role}@novatech-fictional.internal",
                hashed_password=hash_password("Password123!"),
                full_name=f"{role.capitalize()} User",
                employee_id=emp_id,
                role=role,
            )

        async def get_token(role: str) -> str:
            res = await client.post(
                "/api/v1/auth/login",
                json={"email": f"{role}@novatech-fictional.internal", "password": "Password123!"},
            )
            return res.json()["access_token"]

        emp_token = await get_token("employee")
        hr_token = await get_token("hr")
        admin_token = await get_token("admin")

        # Employee: allowed employee zone, denied hr and admin zones
        res_emp_emp = await client.get(
            "/api/v1/test/employee-zone", headers={"Authorization": f"Bearer {emp_token}"}
        )
        assert res_emp_emp.status_code == status.HTTP_200_OK

        res_emp_hr = await client.get(
            "/api/v1/test/hr-zone", headers={"Authorization": f"Bearer {emp_token}"}
        )
        assert res_emp_hr.status_code == status.HTTP_403_FORBIDDEN

        res_emp_admin = await client.get(
            "/api/v1/test/admin-zone", headers={"Authorization": f"Bearer {emp_token}"}
        )
        assert res_emp_admin.status_code == status.HTTP_403_FORBIDDEN

        # HR: allowed employee and hr zones, denied admin zone
        res_hr_hr = await client.get(
            "/api/v1/test/hr-zone", headers={"Authorization": f"Bearer {hr_token}"}
        )
        assert res_hr_hr.status_code == status.HTTP_200_OK

        res_hr_admin = await client.get(
            "/api/v1/test/admin-zone", headers={"Authorization": f"Bearer {hr_token}"}
        )
        assert res_hr_admin.status_code == status.HTTP_403_FORBIDDEN

        # Admin: allowed all zones
        res_admin_admin = await client.get(
            "/api/v1/test/admin-zone", headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert res_admin_admin.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_employee_data_isolation_enforcement(test_app) -> None:
    """Verify an employee is strictly blocked from accessing another employee's records."""
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        user_repo = test_app.dependency_overrides[get_user_repository]()

        # Seed two employees and one HR user
        user_repo.create_user(
            email="emp1@novatech-fictional.internal",
            hashed_password=hash_password("Password123!"),
            full_name="Employee One",
            employee_id="EMP-1001",
            role="employee",
        )
        user_repo.create_user(
            email="emp2@novatech-fictional.internal",
            hashed_password=hash_password("Password123!"),
            full_name="Employee Two",
            employee_id="EMP-1002",
            role="employee",
        )
        user_repo.create_user(
            email="hr1@novatech-fictional.internal",
            hashed_password=hash_password("Password123!"),
            full_name="HR Specialist",
            employee_id="EMP-1003",
            role="hr",
        )

        res_emp1 = await client.post(
            "/api/v1/auth/login",
            json={"email": "emp1@novatech-fictional.internal", "password": "Password123!"},
        )
        res_hr = await client.post(
            "/api/v1/auth/login",
            json={"email": "hr1@novatech-fictional.internal", "password": "Password123!"},
        )

        emp1_token = res_emp1.json()["access_token"]
        hr_token = res_hr.json()["access_token"]

        # EMP-1001 viewing EMP-1001 records -> 200 OK
        res_own = await client.get(
            "/api/v1/test/records/EMP-1001", headers={"Authorization": f"Bearer {emp1_token}"}
        )
        assert res_own.status_code == status.HTTP_200_OK

        # EMP-1001 attempting to view EMP-1002 records -> 403 Forbidden
        res_cross = await client.get(
            "/api/v1/test/records/EMP-1002", headers={"Authorization": f"Bearer {emp1_token}"}
        )
        assert res_cross.status_code == status.HTTP_403_FORBIDDEN
        assert "only access your own employee records" in res_cross.json()["detail"]

        # HR viewing EMP-1002 records -> 200 OK
        res_hr_cross = await client.get(
            "/api/v1/test/records/EMP-1002", headers={"Authorization": f"Bearer {hr_token}"}
        )
        assert res_hr_cross.status_code == status.HTTP_200_OK
