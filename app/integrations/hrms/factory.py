"""Factory provider for configuring active HRMS adapter implementation."""

import logging
from typing import Literal

from app.core.config import get_settings
from app.core.exceptions import ConfigurationError
from app.integrations.hrms.base import BaseHRMSAdapter
from app.integrations.hrms.http_adapter import HttpHRMSAdapter
from app.integrations.hrms.mock_adapter import MockHRMSAdapter

logger = logging.getLogger(__name__)


def create_hrms_adapter(adapter_type: str | None = None) -> BaseHRMSAdapter:
    """Instantiate and return the configured HRMS adapter (mock or http)."""
    settings = get_settings()
    target_type = (adapter_type or settings.hrms_adapter_type).strip().lower()
    env = settings.app_env.strip().lower()

    if target_type == "http":
        logger.info("Initializing HTTP HRMS adapter targeting %s", settings.hrms_api_base_url)
        return HttpHRMSAdapter(
            base_url=settings.hrms_api_base_url,
            timeout=settings.hrms_api_timeout_seconds,
        )

    if env in ("production", "staging"):
        raise ConfigurationError(
            f"Production/staging environment '{env}' forbids MockHRMSAdapter. "
            f"Cannot initialize adapter type '{target_type}'. A real enterprise HRMS adapter is required."
        )

    if target_type == "mock":
        logger.info("Initializing in-memory Mock HRMS adapter with NovaTech sample dataset")
        return MockHRMSAdapter()

    raise ConfigurationError(f"Unrecognized HRMS adapter type '{target_type}'.")
