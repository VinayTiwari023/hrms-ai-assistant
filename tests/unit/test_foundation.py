"""Foundation verification smoke tests for HRMS AI Assistant backend."""

import sqlite3
import pytest
import httpx
from pydantic import ValidationError

from app.core.config import Settings
from app.core.exceptions import ResourceNotFoundError
from app.integrations.hrms.mock_adapter import MockHRMSAdapter
from app.integrations.ollama.client import ChatTurnResult, OllamaClient, ToolCall, ToolCallFunction
from app.main import create_app
from app.schemas.health import HealthResponse, ReadyResponse
from app.schemas.hrms import AttendanceToday, EmployeeProfile, LeaveBalance


@pytest.mark.asyncio
async def test_fastapi_app_startup_and_health_endpoint() -> None:
    """Verify application initializes and responds to /health."""
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        validated = HealthResponse(**data)
        assert validated.status == "healthy"
        assert validated.app_name == "HRMS AI Assistant"


@pytest.mark.asyncio
async def test_readiness_probe_endpoint() -> None:
    """Verify /health/ready probe reports component readiness."""
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health/ready")
        assert response.status_code == 200
        data = response.json()
        validated = ReadyResponse(**data)
        assert validated.ready is True
        names = [comp.name for comp in validated.components]
        assert "database" in names
        assert "hrms_adapter" in names


def test_configuration_loading() -> None:
    """Verify Pydantic Settings loads valid defaults and parses CORS origins."""
    settings = Settings(
        app_name="Test Assistant",
        cors_origins=["http://localhost:3000", "http://testserver"],
    )
    assert settings.app_name == "Test Assistant"
    assert "http://localhost:3000" in settings.cors_origins
    assert settings.ollama_chat_model == "qwen3:4b"
    assert settings.ollama_embed_model == "nomic-embed-text"


def test_pydantic_schema_validation() -> None:
    """Verify schema models enforce data types and constraints."""
    # Valid profile
    profile = EmployeeProfile(
        employee_id="EMP-1001",
        first_name="Alex",
        last_name="Rivera",
        full_name="Alex Rivera",
        email="alex@novatech-fictional.internal",
        department="Engineering",
        designation="Software Engineer",
        joining_date="2024-01-01",
    )
    assert profile.employee_id == "EMP-1001"
    assert profile.role == "employee"

    # Invalid profile (missing required field)
    with pytest.raises(ValidationError):
        EmployeeProfile(first_name="Alex")  # Missing required employee_id, last_name, etc.


def test_sqlite_connection_and_execution() -> None:
    """Verify SQLite database operations function in memory."""
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE smoke_test (id INTEGER PRIMARY KEY, item_name TEXT NOT NULL);")
    cursor.execute("INSERT INTO smoke_test (item_name) VALUES ('foundation_verified');")
    conn.commit()

    cursor.execute("SELECT item_name FROM smoke_test WHERE id = 1;")
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == "foundation_verified"
    conn.close()


@pytest.mark.asyncio
async def test_mock_hrms_adapter() -> None:
    """Verify MockHRMSAdapter returns expected fictional data without external calls."""
    adapter = MockHRMSAdapter()

    # 1. Profile
    profile = await adapter.get_profile("EMP-1001")
    assert profile.full_name == "Alex Rivera"
    assert profile.department == "Engineering"

    # 2. Attendance
    att = await adapter.get_attendance_today("EMP-1001")
    assert att.status == "Present"
    assert att.check_in == "09:12"

    # 3. Leave Balances
    leaves = await adapter.get_leave_balance("EMP-1001")
    assert len(leaves.balances) >= 1
    assert any(b.leave_type == "Annual Leave" for b in leaves.balances)

    # 4. Unknown Employee raises ResourceNotFoundError
    with pytest.raises(ResourceNotFoundError):
        await adapter.get_profile("NON_EXISTENT_ID")


@pytest.mark.asyncio
async def test_mock_ollama_integration() -> None:
    """Verify OllamaClient structure and simulated inference output."""
    client = OllamaClient(base_url="http://127.0.0.1:11434")

    # When Ollama is offline, check_health safely returns False without unhandled exceptions
    is_healthy = await client.check_health()
    assert isinstance(is_healthy, bool)

    # Test ChatTurnResult data class and tool call parsing
    mock_turn = ChatTurnResult(
        content="I have retrieved your attendance details.",
        thinking="Checking tool result for EMP-1001",
        tool_calls=[
            ToolCall(
                id="call_001",
                type="function",
                function=ToolCallFunction(
                    name="get_my_attendance_today",
                    arguments={},
                ),
            )
        ],
        model="qwen3:4b",
    )
    assert mock_turn.has_tool_calls is True
    assert mock_turn.tool_calls[0].function.name == "get_my_attendance_today"
    assert mock_turn.thinking == "Checking tool result for EMP-1001"
