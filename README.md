# RESTScope

RESTScope is currently a small Python package for parsing Swagger 2.0 and
OpenAPI 3.x specifications into a normalized intermediate representation.

## Configuration

The parser-only package uses a short optional `.env` file:

```env
LOG_LEVEL=INFO
DATA_DIR=./data
# LOG_FILE=./data/logs/restscope.log

DB_URL=sqlite:///./data/restscope.db
DB_ECHO=false

THINK_PROVIDER=openai_compatible
THINK_MODEL=glm-4.5-air
THINK_API_KEY=your-api-key
THINK_BASE_URL=https://open.bigmodel.cn/api/paas/v4

FAST_PROVIDER=openai_compatible
FAST_MODEL=glm-4.7-flash
# FAST_PROVIDER, FAST_API_KEY, and FAST_BASE_URL default to THINK_* values
```

## Development

```bash
uv sync
uv run pytest
```

## Database

The MVP database layer lives in `restscope.db` and provides SQLAlchemy ORM
mappings, repositories, a UnitOfWork transaction boundary, and packaged Alembic
migrations for the MVP tables.

## LLM

The MVP LLM layer lives in `restscope.llm`. It provides provider-neutral request
and response schemas, a `FakeProvider` for offline tests, an OpenAI-compatible
provider, model selection for thinking/fast roles, structured output validation,
and a safe tool-call shell in `restscope.capabilities`.

Provider calls are routed through `LLMClient`; providers normalize responses but
do not execute tools or write database rows.
