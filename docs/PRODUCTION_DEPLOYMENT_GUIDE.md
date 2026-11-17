# Production Deployment Guide — HRMS AI Assistant

## 1. Overview & Positioning

This guide outlines deployment topologies, operational hardening, and security invariants for deploying the **HRMS AI Assistant** into production enterprise environments.

> [!IMPORTANT]
> The HRMS AI Assistant is a backend-only AI integration service. It is designed to run behind an enterprise reverse proxy / API gateway, interfacing with an existing enterprise HRMS system via the `HttpHRMSAdapter` and an inference engine (Ollama or OpenAI-compatible server) hosted on appropriate GPU infrastructure.

---

## 2. Infrastructure & Sizing Requirements

### 2.1 Backend API Server
- **Runtime**: Python 3.11+ (verified through Python 3.15.2)
- **CPU**: 2–4 vCPUs
- **Memory**: 4 GB RAM (base application and local in-memory caches)
- **Disk**: 10–20 GB SSD (for SQLite WAL database and temporary document staging)

### 2.2 LLM & Embedding Inference Infrastructure
- **CPU-Only Warning**: Running 4B+ parameter models (e.g. `qwen3:4b`) on consumer or CPU-only hardware results in substantial latency (~113.7 s Time-to-First-Token in benchmarks). **CPU-only execution is strongly discouraged for production.**
- **Recommended GPU Sizing**:
  - Chat Model (`qwen3:4b` or 7B–14B quantized models): Minimum 8 GB–16 GB VRAM (e.g., NVIDIA T4, L4, A10G, or RTX 4090).
  - Embeddings (`nomic-embed-text`): Minimum 2 GB VRAM or high-performance CPU inference node.
  - Dedicated Inference Node: Host Ollama, vLLM, or TGI on a dedicated GPU instance separated from the FastAPI backend.

### 2.3 Vector Database (Qdrant)
- **Development/Evaluation**: Local embedded Qdrant (`QDRANT_MODE=local`, stored at `./storage/qdrant`).
- **Production**: Distributed Qdrant cluster or managed Qdrant Cloud (`QDRANT_MODE=remote`). Minimum 2 nodes, 4 GB RAM per node for knowledge bases under 100,000 chunks.

---

## 3. Mandatory Production Environment Configuration

In `production` and `staging` environments, `app.core.config.validate_security_configuration()` enforces strict security invariants. Any violation causes the application to abort startup immediately (`ConfigurationError`).

| Setting | Production Requirement | Description |
| :--- | :--- | :--- |
| `APP_ENV` | `production` or `staging` | Activates strict production validation routines. |
| `SECRET_KEY` | 32+ characters, cryptographically random | Must not contain substrings like `dev-`, `changeme`, `default`, `example`. |
| `HRMS_ADAPTER_TYPE` | `http` | **`mock` adapter is strictly forbidden in production.** |
| `HRMS_API_BASE_URL` | Valid HTTPS URL | Must not contain placeholder substrings (`placeholder`, `example.com`, `dummy`). |
| `CORS_ORIGINS` | Explicit domain list | **Wildcard `'*'` is forbidden** when credentialed requests are allowed. |
| `ALLOW_PUBLIC_REGISTRATION` | `false` | Public self-registration is disabled. Employees are provisioned by admin or SSO. |
| `ENABLE_API_DOCS` | `false` | Interactive Swagger (`/docs`, `/redoc`) is suppressed in production. |
| `HRMS_MUTATIONS_ENABLED` | `false` | Assistant operates strictly read-only unless explicit write workflows are audited. |
| `QDRANT_MODE` | `remote` | Vector store uses remote cluster URL and authentication key. |
| `QDRANT_URL` | Valid URL (e.g. `https://qdrant.internal:6333`) | Required when `QDRANT_MODE=remote`. |

### 3.1 Generating a Cryptographically Secure Secret Key

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## 4. Production Topology & Reverse Proxy Configuration

```
┌─────────────────────────────────────────────────────────────┐
│                       Internet / WAN                        │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTPS (TLS 1.3)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│               Enterprise Reverse Proxy / Ingress            │
│                 (Nginx / Traefik / AWS ALB)                 │
└──────────────┬───────────────────────────────┬──────────────┘
               │                               │
       HTTP/1.1 (Internal)             HTTP/1.1 (Internal)
               ▼                               ▼
┌─────────────────────────────┐ ┌─────────────────────────────┐
│  HRMS AI Assistant (Node 1) │ │  HRMS AI Assistant (Node 2) │
│   Uvicorn / Gunicorn ASGI   │ │   Uvicorn / Gunicorn ASGI   │
└──────────────┬──────────────┘ └──────────────┬──────────────┘
               │                               │
               ├───────────────────────────────┤
               │                               │
               ▼                               ▼
┌─────────────────────────────┐ ┌─────────────────────────────┐
│      Enterprise HRMS API    │ │   GPU Inference Cluster     │
│   (Protected Private VPC)   │ │  (Ollama / vLLM on NVIDIA)  │
└─────────────────────────────┘ └─────────────────────────────┘
```

### 4.1 Nginx Reverse Proxy Configuration (with SSE Streaming Support)

Ensure proxy buffering is disabled for streaming paths to prevent buffering chunked Server-Sent Events (SSE):

```nginx
server {
    listen 443 ssl http2;
    server_name hrms-ai.yourcompany.internal;

    ssl_certificate     /etc/ssl/certs/hrms-ai.crt;
    ssl_certificate_key /etc/ssl/private/hrms-ai.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # Security Headers
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;

    # Standard API endpoints
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Real-Time SSE Streaming Endpoint
    location /api/v1/chat/stream {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Disable proxy buffering for Server-Sent Events
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        chunked_transfer_encoding on;
    }
}
```

---

## 5. Process Supervision (Systemd Service)

Create `/etc/systemd/system/hrms-ai-assistant.service`:

```ini
[Unit]
Description=HRMS AI Assistant Backend Service
After=network.target

[Service]
Type=simple
User=hrms-app
Group=hrms-app
WorkingDirectory=/opt/hrms-ai-assistant
EnvironmentFile=/etc/hrms-ai-assistant/.env
ExecStart=/opt/hrms-ai-assistant/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4 --proxy-headers
Restart=always
RestartSec=5s
LimitNOFILE=65535

# Systemd Hardening Sandboxing
ProtectSystem=full
ProtectHome=true
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hrms-ai-assistant
```

---

## 6. Health & Readiness Monitoring

Integrate monitoring systems (Kubernetes probes, Prometheus Blackbox, Datadog) with standard health endpoints:

### 6.1 Liveness Probe
- **Endpoint**: `GET /health`
- **Expected Status**: `200 OK`
- **Payload**: `{"status": "healthy", "version": "0.1.0"}`
- **Action on Failure**: Restart process or container.

### 6.2 Readiness Probe
- **Endpoint**: `GET /health/ready`
- **Expected Status**: `200 OK`
- **Evaluates**:
  - Primary SQLite persistence connection.
  - HRMS adapter connectivity (via `adapter.health_check()`).
  - Ollama inference server reachability (`/api/tags`).
- **Action on Failure**: Remove node from load balancer rotation until dependencies recover.

---

## 7. Backup and Recovery

1. **SQLite Database**:
   - Backup the SQLite database safely using the online backup API or SQLite CLI:
     ```bash
     sqlite3 /opt/hrms-ai-assistant/storage/hrms_assistant.db ".backup '/backup/hrms_assistant_$(date +%Y%m%d_%H%M%S).db'"
     ```
   - Never copy `hrms_assistant.db` directly while active without accounting for `-wal` and `-shm` write-ahead log files.
2. **Qdrant Vector Database**:
   - Utilize Qdrant Snapshot API:
     ```bash
     curl -X POST "http://qdrant.internal:6333/collections/hrms_knowledge/snapshots"
     ```
3. **Secrets & Configurations**:
   - Store `.env` in an enterprise secret manager (HashiCorp Vault, AWS Secrets Manager, Azure Key Vault). Never commit secrets to version control.
