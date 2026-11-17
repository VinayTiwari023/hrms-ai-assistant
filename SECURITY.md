# Security Policy — HRMS AI Assistant

## Supported Versions

We provide security updates and patches for the following versions:

| Version | Supported          |
| :---    | :---               |
| 0.1.x   | :white_check_mark: |
| < 0.1   | :x:                |

---

## 1. Reporting a Vulnerability

We appreciate the responsible disclosure of security vulnerabilities. If you discover a vulnerability in **HRMS AI Assistant**, please do **NOT** open a public issue or discussion.

### Private Disclosure Process
1. Report security issues by contacting the repository maintainers privately via GitHub Security Advisories or via email at `security-alert@example.com` (replace with maintainer email upon repository publication).
2. Include the following details:
   - Type of vulnerability (e.g. Broken Object Level Authorization, prompt injection bypassing boundaries, credential disclosure).
   - Step-by-step instructions or proof-of-concept to reproduce the issue.
   - Affected components, configurations, and version.
   - Any proposed remediation or patch.
3. We will acknowledge receipt of your report within 48 hours and provide an estimated timeline for remediation.

---

## 2. Core Security Invariants

All users, integrators, and contributors must respect the following security guarantees:

1. **Zero-Trust LLM**: The Large Language Model is an untrusted presentation layer. It is never granted authority to evaluate permissions or enforce access boundaries.
2. **Strict Identity Derivation**: Personal employee tools bind strictly to the verified claims in the JWT Bearer token (`current_user.employee_id`). Client-supplied or model-generated IDs are rejected for personal scopes.
3. **Server-Side Authorization**: Roles (`employee`, `hr`, `admin`) and department boundaries are enforced by FastAPI dependencies and repository queries before tools run or documents are retrieved.
4. **Production Invariants**:
   - `MockHRMSAdapter` is strictly blocked in `production` and `staging` modes.
   - Wildcard CORS (`*`) is forbidden when credentials are permitted.
   - Default, weak (< 32 chars), or placeholder `SECRET_KEY` values are rejected at startup.
   - Interactive Swagger API docs (`/docs`, `/redoc`) are disabled in production by default.
   - Public self-registration is disabled by default in production.
   - Mutations remain disabled (`HRMS_MUTATIONS_ENABLED=false`).

For in-depth architectural details, see:
- [docs/SECURITY.md](file:///C:/Projects/hrms-ai-assistant/docs/SECURITY.md) — Comprehensive Security & Compliance Specification
- [docs/SECURITY_AND_LIMITATIONS.md](file:///C:/Projects/hrms-ai-assistant/docs/SECURITY_AND_LIMITATIONS.md) — Security Architecture & Operational Limitations
