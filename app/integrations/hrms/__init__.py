"""Generic HRMS adapter interfaces, implementations, and factory."""

from app.integrations.hrms.base import BaseHRMSAdapter
from app.integrations.hrms.factory import create_hrms_adapter
from app.integrations.hrms.http_adapter import HttpHRMSAdapter
from app.integrations.hrms.mock_adapter import MockHRMSAdapter

__all__ = [
    "BaseHRMSAdapter",
    "MockHRMSAdapter",
    "HttpHRMSAdapter",
    "create_hrms_adapter",
]
