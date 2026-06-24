# RESTScope

RESTScope is currently a small Python package for parsing Swagger 2.0 and
OpenAPI 3.x specifications into a normalized intermediate representation.

## Configuration

The parser-only package uses a short optional `.env` file:

```env
LOG_LEVEL=INFO
DATA_DIR=./data
# LOG_FILE=./data/logs/restscope.log

THINK_MODEL=glm-4.5-air
THINK_API_KEY=your-api-key
THINK_BASE_URL=https://open.bigmodel.cn/api/paas/v4

FAST_MODEL=glm-4.7-flash
# FAST_API_KEY defaults to THINK_API_KEY
# FAST_BASE_URL defaults to THINK_BASE_URL
```

## Development

```bash
uv sync
uv run pytest
```
