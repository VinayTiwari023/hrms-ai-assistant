"""Standard domain exceptions for the HRMS AI Assistant backend."""


class AppError(Exception):
    """Base exception for all application errors."""

    def __init__(self, message: str, code: str = "INTERNAL_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class ConfigurationError(AppError):
    """Raised when configuration values are missing or invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="CONFIG_ERROR")


class AuthenticationError(AppError):
    """Raised when authentication fails or credential is invalid."""

    def __init__(self, message: str = "Invalid authentication credentials") -> None:
        super().__init__(message, code="AUTH_ERROR")


class AuthorizationError(AppError):
    """Raised when principal lacks permission to perform the requested operation."""

    def __init__(self, message: str = "Access denied: insufficient permissions") -> None:
        super().__init__(message, code="FORBIDDEN")


class ResourceNotFoundError(AppError):
    """Raised when a requested resource does not exist."""

    def __init__(self, message: str = "Requested resource not found") -> None:
        super().__init__(message, code="NOT_FOUND")


class HRMSAdapterError(AppError):
    """Raised when an HRMS adapter operation encounters a failure."""

    def __init__(self, message: str, code: str = "HRMS_ERROR") -> None:
        super().__init__(message, code=code)


class HRMSMutationBlockedError(HRMSAdapterError):
    """Raised when a mutation operation is requested while mutations are disabled."""

    def __init__(self, operation: str) -> None:
        super().__init__(
            f"HRMS mutation '{operation}' is blocked. Mutations are currently disabled by configuration.",
            code="MUTATION_BLOCKED",
        )


class LLMServiceError(AppError):
    """Raised when communication with the LLM runtime fails."""

    def __init__(self, message: str, code: str = "LLM_ERROR") -> None:
        super().__init__(message, code=code)


class VectorStoreError(AppError):
    """Raised when vector database operations fail."""

    def __init__(self, message: str, code: str = "VECTOR_ERROR") -> None:
        super().__init__(message, code=code)


class IngestionError(AppError):
    """Raised when document ingestion or processing encounters a failure."""

    def __init__(self, message: str, code: str = "INGESTION_ERROR") -> None:
        super().__init__(message, code=code)


class DocumentParsingError(IngestionError):
    """Raised when extracting text from an uploaded document fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="PARSE_ERROR")


class UnsupportedFormatError(IngestionError):
    """Raised when an uploaded document format is not supported."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="UNSUPPORTED_FORMAT")


class DuplicateDocumentError(IngestionError):
    """Raised when a duplicate document is uploaded."""

    def __init__(self, message: str = "Document with identical content has already been ingested.") -> None:
        super().__init__(message, code="DUPLICATE_DOCUMENT")


class EmbeddingError(AppError):
    """Raised when vector embedding generation fails."""

    def __init__(self, message: str, code: str = "EMBEDDING_ERROR") -> None:
        super().__init__(message, code=code)


class RetrievalError(AppError):
    """Raised when semantic retrieval fails."""

    def __init__(self, message: str, code: str = "RETRIEVAL_ERROR") -> None:
        super().__init__(message, code=code)
