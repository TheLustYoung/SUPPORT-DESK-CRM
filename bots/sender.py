"""Runs render plans (backend.services.render) with aiogram: the welcome message, FAQ answers, menu pages."""
from aiogram import Bot, methods
from aiogram.types import (BufferedInputFile, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo, LinkPreviewOptions,
                           Message)
from bots.client import ApiClient

METHODS = {"sendMessage": methods.SendMessage, "sendPhoto": methods.SendPhoto, "sendVideo": methods.SendVideo,
           "sendAudio": methods.SendAudio, "sendVoice": methods.SendVoice, "sendDocument": methods.SendDocument,
           "sendAnimation": methods.SendAnimation}
VIDEO = (".mp4", ".mov", ".webm")


def file_id_of(message: Message, field: str) -> str | None:
    if field == "photo" and message.photo:
        return message.photo[-1].file_id
    for name in (field, "document", "video", "audio", "voice", "animation"):
        item = getattr(message, name, None)
        if item is not None and hasattr(item, "file_id"):
            return item.file_id
    return None


class Sender:
    def __init__(self, bot: Bot, api: ApiClient):
        self.bot, self.api = bot, api
        self.file_ids: dict[int, str] = {}  # Telegram's ids of media library files already uploaded by this bot

    async def _input(self, asset_id: int):
        if asset_id in self.file_ids:
            return self.file_ids[asset_id], None
        data, name, cached = await self.api.asset(asset_id)
        if cached:
            self.file_ids[asset_id] = cached
            return cached, name
        return BufferedInputFile(data, filename=name), name

    async def _remember(self, asset_id: int, file_id: str | None) -> None:
        if file_id and self.file_ids.get(asset_id) != file_id:
            self.file_ids[asset_id] = file_id
            await self.api.post(f"assets/{asset_id}/file-id", {"file_id": file_id})

    @staticmethod
    def _fields(data: dict) -> dict:
        fields = {k: v for k, v in data.items() if v is not None}
        if isinstance(fields.get("reply_markup"), dict):
            fields["reply_markup"] = InlineKeyboardMarkup.model_validate(fields["reply_markup"])
        if isinstance(fields.get("link_preview_options"), dict):
            fields["link_preview_options"] = LinkPreviewOptions(**fields["link_preview_options"])
        return fields

    async def run(self, chat_id: int, calls: list[dict]) -> list:
        results = []
        for call in calls:
            if call["method"] == "sendMediaGroup":
                items = []
                for item in call["media"]:
                    media, name = await self._input(item["asset"])
                    kind = InputMediaVideo if name and name.lower().endswith(VIDEO) else InputMediaPhoto
                    items.append(kind(media=media, caption=item.get("caption"), parse_mode="HTML" if item.get("caption") else None))
                sent = await self.bot(methods.SendMediaGroup(chat_id=chat_id, media=items))
                for item, message in zip(call["media"], sent):
                    await self._remember(item["asset"], file_id_of(message, "video" if message.video else "photo"))
                results.append(sent)
                continue
            fields = self._fields(call["data"])
            if call.get("asset"):
                fields[call["field"]], _ = await self._input(call["asset"])
            message = await self.bot(METHODS[call["method"]](chat_id=chat_id, **fields))
            if call.get("asset"):
                await self._remember(call["asset"], file_id_of(message, call["field"]))
            results.append(message)
        return results
