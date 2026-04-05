# contextql-server

The networked context resolution plane for ContextQL — a FastAPI service that hosts the context catalog, brokers federated context resolution across MCP and REMOTE providers, and exposes query execution over HTTP.

## Vision

While [`contextql`](https://github.com/contextql-lang/contextql) provides the context resolution engine (algebra, execution, scoring), `contextql-server` is the **control plane** that makes it operational:

- **Context Catalog** — Central registry for context definitions, lifecycle states, and metadata
- **Federation Broker** — Routes context requests to distributed MCP and REMOTE providers
- **Identity Resolution** — Cross-system entity namespace for joining across organizational boundaries
- **Governance** — Multi-tenant access control, audit trail, and classification enforcement

## Current Implementation (v0.1)

The server currently provides:

- `POST /query` — Execute ContextQL queries against the engine
- `GET /health` — Engine status, version, available tables and contexts
- Built-in provider registration (FraudDetectionMCP, PriorityMCP, JiraRemoteProvider)
- Mock providers for testing (EchoMCPProvider, StaticRemoteProvider)
- Configurable via `CQL_` environment variables
- Execution timing and diagnostics in response metadata

## Quick Start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Start server with demo engine
uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000

# Query
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query": "SELECT invoice_id, amount FROM invoices WHERE CONTEXT IN (overdue_invoice) ORDER BY CONTEXT DESC LIMIT 5;"}'
```

## Configuration

All settings use the `CQL_` environment variable prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `CQL_HOST` | `0.0.0.0` | Bind address |
| `CQL_PORT` | `8000` | Listen port |
| `CQL_LOG_LEVEL` | `INFO` | Log verbosity |
| `CQL_DATABASE` | `:memory:` | DuckDB database path |
| `CQL_USE_DEMO` | `True` | Load demo dataset on startup |
| `CQL_MCP_TIMEOUT_MS` | `30000` | MCP provider timeout |
| `CQL_REMOTE_TIMEOUT_MS` | `30000` | REMOTE provider timeout |
| `CQL_REGISTER_MOCK_PROVIDERS` | `True` | Register echo/static mock providers |

## Architecture

```
app/
├── main.py           FastAPI app with lifespan (startup/shutdown)
├── config.py         Pydantic Settings (CQL_ prefix)
├── dependencies.py   Engine + QueryService dependency injection
├── api/
│   ├── routes.py     Combined router
│   ├── health.py     GET /health
│   └── query.py      POST /query
├── core/
│   └── engine.py     EngineManager (contextql.Engine wrapper)
├── models/
│   ├── request.py    QueryRequest schema
│   └── response.py   QueryResponse, QueryMeta, HealthResponse
├── services/
│   └── query_service.py  Query execution + timing
├── providers/
│   ├── registry.py   Default provider registration
│   └── mock.py       EchoMCPProvider, StaticRemoteProvider
└── utils/
    └── logging.py    JSON-formatted logging
```

## License

Apache 2.0 — Copyright (c) 2026 Anton du Plessis
