"""Texts and languages as configured in the dashboard, for the bot, the workers and the API."""
from sqlalchemy import select
from backend.models import BotText, Language
from backend.services.i18n import FALLBACK, Translator


def languages(db, enabled_only: bool = True) -> list[Language]:
    every = list(db.scalars(select(Language).order_by(Language.position, Language.code)))
    enabled = [lang for lang in every if lang.enabled]
    return (enabled or every) if enabled_only else every


def default_language(db) -> str:
    every = languages(db, enabled_only=False)
    return next((lang.code for lang in every if lang.is_default), every[0].code if every else FALLBACK)


def translator(db) -> Translator:
    every = languages(db, enabled_only=False)
    custom = {(row.lang, row.key): row.value for row in db.scalars(select(BotText))}
    return Translator(custom, default_language(db), {lang.code for lang in every if lang.rtl})
