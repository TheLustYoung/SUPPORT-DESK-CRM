"""Central FastAPI backend: the dashboard API, the bot's internal API and health checks."""
import logging
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from backend import observability
from backend.api import routers
from backend.config import settings
from backend.services.security import DomainError

if not logging.getLogger().handlers:  # the local runner configures its own readable logs
    observability.setup_logging("api")
observability.setup("api")

# The interactive API docs are for development and testing installations; in production the map of every endpoint
# is not published (docs/API.md and docs/openapi.json in the repository describe the API).
_public_docs = settings().environment != "production"
app = FastAPI(title="Support Platform", version="0.1.0", docs_url="/api/docs" if _public_docs else None,
              openapi_url="/api/openapi.json" if _public_docs else None, redoc_url=None,
              description="Dashboard API (/api/admin, cookie session), bot API (/internal, service key) and health checks.")
for router in routers:
    app.include_router(router)


@app.exception_handler(DomainError)
async def domain_error(_request, error: DomainError):
    return JSONResponse({"detail": error.code}, status_code=error.status)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if request.url.path.startswith(("/api/", "/internal/")):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


__all__ = ["app", "settings"]
