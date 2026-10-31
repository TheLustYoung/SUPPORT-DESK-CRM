import httpx
from backend.config import settings


class ApiError(Exception):
    """Error code from the backend (a text key such as 'rate_limit' or 'banned')."""


class ApiClient:
    """The bot holds no business logic and never touches the database: every decision is made by the backend."""

    def __init__(self, base_url: str | None = None, transport=None):
        cfg = settings()
        self.client = httpx.AsyncClient(base_url=base_url or cfg.api_internal_url, timeout=20, transport=transport,
                                        headers={"X-Service-Key": cfg.bot_service_key.get_secret_value()})

    async def _check(self, response: httpx.Response):
        if response.is_error:
            try:
                detail = response.json().get("detail", "failed")
            except ValueError:
                detail = "failed"
            raise ApiError(detail if isinstance(detail, str) else "failed")
        return response

    async def post(self, route: str, data: dict | None = None) -> dict | list:
        return (await self._check(await self.client.post("/internal/" + route, json=data or {}))).json()

    async def get(self, route: str) -> dict | list:
        return (await self._check(await self.client.get("/internal/" + route))).json()

    async def asset(self, asset_id: int) -> tuple[bytes, str, str]:
        """(bytes, file name, cached Telegram file_id) of a media library file."""
        response = await self._check(await self.client.get(f"/internal/assets/{asset_id}"))
        name = response.headers.get("content-disposition", "").rpartition("filename=")[2].strip('"') or f"file-{asset_id}"
        return response.content, name, response.headers.get("x-file-id", "")

    async def close(self):
        await self.client.aclose()
