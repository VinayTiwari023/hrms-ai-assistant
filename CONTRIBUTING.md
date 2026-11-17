# Contributing to HRMS AI Assistant

Thank you for your interest in contributing to the **HRMS AI Assistant** project!

This project is an open-source, backend-only reference implementation providing generic AI-powered conversational assistance, role-aware tool calling, and RAG knowledge retrieval for enterprise HRMS systems.

---

## 1. Code of Conduct

All contributors, maintainers, and community members are expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md). Please ensure respectful and constructive collaboration.

---

## 2. Development Setup

### 2.1 Prerequisites
- **Python**: 3.11 or higher (tested through Python 3.15.2).
- **Git**: For version control.
- *(Optional)* **Ollama**: For running live local inference tests with `qwen3:4b` and `nomic-embed-text`. Automated test suites execute offline using deterministic mocks.

### 2.2 Local Environment Setup

1. Fork and clone the repository:
   ```bash
   git clone https://github.com/<owner>/hrms-ai-assistant.git
   cd hrms-ai-assistant
   ```

2. Create and activate a Python virtual environment:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\activate
   ```
   *(On Linux/macOS: `source .venv/bin/activate`)*

3. Install project dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Create your local environment configuration:
   ```powershell
   Copy-Item .env.example .env
   ```
   *(On Linux/macOS: `cp .env.example .env`)*

---

## 3. Running Verification & Tests

Before submitting any code, verify that all dependencies and automated tests pass cleanly:

### 3.1 Dependency Verification
```bash
python -m pip check
```

### 3.2 Automated Test Suite
Run the full test suite (168 unit and API integration tests):
```bash
python -m pytest tests/ -v
```

### 3.3 Live Ollama Tests (Optional)
If a local Ollama daemon is running with `qwen3:4b` and `nomic-embed-text`:
```powershell
$env:RUN_LIVE_OLLAMA_TESTS="1"
python -m pytest tests/live/ -v
$env:RUN_LIVE_OLLAMA_TESTS=""
```

### 3.4 Operational Benchmark
Verify latency and memory footprint:
```bash
python scripts/benchmark_performance.py
```

---

## 4. Development Principles & Security Invariants

All contributions must uphold the core architectural invariants of this project:

1. **Zero-Trust LLM**:
   - The LLM is an untrusted client in authorization decisions.
   - Never rely on prompt instructions alone to enforce access control or user identity.
   - Enforce all permissions, roles, and boundaries server-side using FastAPI dependencies.
2. **Strict Identity Derivation**:
   - Self-service tools must derive `employee_id` strictly from verified JWT claims (`current_user.employee_id`).
   - Never accept client-supplied or LLM-supplied employee IDs for personal queries.
3. **Generic & Reusable**:
   - Do not commit proprietary code, company documents, employee records, internal URLs, or vendor-locked logic.
   - Use generic adapter abstractions (`BaseHRMSAdapter`) and standard schemas (`app.schemas.hrms`).
   - All sample records must use fictional personas (e.g. NovaTech Innovations).
4. **Data Isolation**:
   - Cross-employee records and department-isolated documents must remain strictly protected.
5. **Zero-Leak Logging**:
   - Never log passwords, Bearer tokens, raw document bodies, or secret credentials.
   - Always use `SanitizingFormatter` or `mask_sensitive_data()`.

---

## 5. Submitting Changes

1. **Create a Feature Branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```
2. **Commit Your Changes**:
   - Write descriptive, concise commit messages.
   - Ensure no unintended files, caches, or `.env` files are tracked (`git status`).
3. **Open a Pull Request**:
   - Describe what the PR accomplishes and why.
   - Reference any relevant issues.
   - Confirm that `pip check` and `pytest tests/` pass completely.

---

## 6. Reporting Security Vulnerabilities

Please do **NOT** open public GitHub issues for security vulnerabilities. Review our [Security Policy](SECURITY.md) for responsible disclosure instructions.
