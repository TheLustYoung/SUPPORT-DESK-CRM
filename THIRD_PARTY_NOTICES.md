# Third-party components

The project's own code is covered by [LICENSE](LICENSE). It depends on the open-source components below, each under
its own licence. They are installed from their official package registries and are not modified.

| Component | Used for | Licence |
|---|---|---|
| FastAPI, Starlette, Pydantic, pydantic-settings | HTTP API, validation, configuration | MIT |
| SQLAlchemy, Alembic | Database access, migrations | MIT |
| psycopg | PostgreSQL driver | LGPL-3.0 (used as an unmodified library) |
| Celery, redis-py | Background jobs, scheduler, queues | BSD-3-Clause / MIT |
| aiogram | Telegram Bot API framework | MIT |
| argon2-cffi, PyJWT, pyotp | Password hashing, sessions, two-factor codes | MIT |
| httpx | Outgoing HTTP | BSD-3-Clause |
| python-multipart | File uploads | Apache-2.0 |
| openpyxl | Excel export | MIT |
| fpdf2 | PDF export | LGPL-3.0 (used as an unmodified library) |
| sentry-sdk | Optional error tracking | MIT |
| uvicorn | ASGI server | BSD-3-Clause |
| React, React DOM | Dashboard UI | MIT |
| qrcode-generator | QR code for 2FA setup | MIT |
| Vite, TypeScript, Sass | Dashboard build | MIT / Apache-2.0 / MIT |
| Inter (via @fontsource) | Dashboard font | SIL Open Font License 1.1 |

Full licence texts ship with each package (`pip show -f <package>`, `node_modules/<package>/LICENSE`).
Telegram is a trademark of its owner; the project is not affiliated with or endorsed by Telegram.
