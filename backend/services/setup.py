"""Initial data: built-in roles (always kept in line with the code) and, on an empty database, the default languages,
ticket statuses with their permitted transitions, SLA targets, departments with categories, the main menu and an
example FAQ tree. Later runs never overwrite what was changed in the dashboard."""
from sqlalchemy import func, select
from backend.config import settings
from backend.models import (BotState, Category, Department, FaqNode, Language, MenuItem, Role, SlaPolicy, StatusTransition,
                            TicketStatus)
from backend.services import faq, options
from backend.services.i18n import KNOWN, normalize
from backend.services.permissions import BUILTIN_ROLES

STATUSES = [  # key, stage, color, notify the user, names
    ("new", "new", "#7aa2ff", False, {"en": "New", "ru": "Новое"}),
    ("open", "open", "#5ec8d8", False, {"en": "Open", "ru": "В работе"}),
    ("pending_agent", "pending_agent", "#f2b866", False, {"en": "Pending agent", "ru": "Ждёт поддержку"}),
    ("pending_user", "pending_user", "#c39bff", True, {"en": "Waiting for your reply", "ru": "Ждёт вашего ответа"}),
    ("escalated", "escalated", "#ff6b81", False, {"en": "Escalated", "ru": "Эскалировано"}),
    ("resolved", "resolved", "#5ad19a", True, {"en": "Resolved", "ru": "Решено"}),
    ("closed", "closed", "#8b8494", True, {"en": "Closed", "ru": "Закрыто"}),
    ("reopened", "reopened", "#ff9f6b", False, {"en": "Reopened", "ru": "Открыто снова"}),
]
ACTIVE = ["open", "pending_agent", "pending_user", "escalated", "resolved", "closed"]
TRANSITIONS = {
    "new": ACTIVE,
    "open": ["pending_agent", "pending_user", "escalated", "resolved", "closed"],
    "pending_agent": ["open", "pending_user", "escalated", "resolved", "closed"],
    "pending_user": ["open", "pending_agent", "escalated", "resolved", "closed"],
    "escalated": ["open", "pending_agent", "pending_user", "resolved", "closed"],
    "resolved": ["closed", "reopened"],
    "closed": ["reopened"],
    "reopened": ["open", "pending_agent", "pending_user", "escalated", "resolved", "closed"],
}
SLA = [("urgent", 30, 240), ("high", 60, 480), ("normal", 240, 1440), ("low", 480, 4320)]
DEPARTMENTS = [
    ({"en": "General questions", "ru": "Общие вопросы"}, []),
    ({"en": "Technical support", "ru": "Техническая поддержка"},
     [({"en": "The app or website doesn't work", "ru": "Не работает приложение или сайт"}, "high"),
      ({"en": "Account and sign-in", "ru": "Аккаунт и вход"}, "normal")]),
    ({"en": "Billing and payments", "ru": "Оплата и счета"},
     [({"en": "Payment problem", "ru": "Проблема с оплатой"}, "high"), ({"en": "Refund", "ru": "Возврат"}, "normal")]),
]
MENU = ["faq", "support", "tickets", "language"]
# The languages of the bot texts shipped in backend/services/locales (the same set as the media platform);
# more can be added in the dashboard (Settings → Languages).
LANGUAGES = ("en", "ar", "bn", "es", "fr", "he", "hi", "pt", "ru", "ur", "zh")


def _text(en: str, ru: str) -> dict:
    return {"blocks": [{"type": "text", "text": {"en": en, "ru": ru}, "assets": [], "enabled": True}], "buttons": []}


FAQ = [  # (title, answer or None for a category, children)
    ({"en": "🚀 Getting started", "ru": "🚀 Начало работы"}, None, [
        ({"en": "How do I create an account?", "ru": "Как создать аккаунт?"},
         _text("Open our website or app, tap <b>Sign up</b> and follow the steps. You'll get a confirmation e-mail.",
               "Откройте наш сайт или приложение, нажмите <b>Регистрация</b> и следуйте шагам. Вы получите письмо с подтверждением."), []),
        ({"en": "I forgot my password", "ru": "Я забыл пароль"},
         _text("On the sign-in screen tap <b>Forgot password</b> and enter your e-mail: we'll send a link to set a new one.",
               "На экране входа нажмите <b>Забыли пароль</b> и введите e-mail: мы пришлём ссылку для нового пароля."), []),
    ]),
    ({"en": "💳 Payments", "ru": "💳 Оплата"}, None, [
        ({"en": "Which payment methods do you accept?", "ru": "Какие способы оплаты вы принимаете?"},
         _text("Bank cards (Visa, Mastercard), Apple Pay and Google Pay.", "Банковские карты (Visa, Mastercard), Apple Pay и Google Pay."), []),
        ({"en": "How do I get a refund?", "ru": "Как вернуть деньги?"},
         _text("Contact support with the payment date and amount. Approved refunds return to the same card within 3–10 working days.",
               "Напишите в поддержку дату и сумму платежа. Одобренный возврат приходит на ту же карту за 3–10 рабочих дней."), []),
    ]),
    ({"en": "🛠 Technical issues", "ru": "🛠 Технические проблемы"}, None, [
        ({"en": "The app doesn't open", "ru": "Приложение не открывается"},
         _text("Update the app to the latest version and restart your phone. If it still doesn't open, contact support and attach a screenshot.",
               "Обновите приложение до последней версии и перезапустите телефон. Если не помогло — напишите в поддержку и приложите скриншот."), []),
    ]),
]


def sync_roles(db) -> None:
    for role_id, (name, description, permissions) in BUILTIN_ROLES.items():
        role = db.get(Role, role_id)
        if role is None:
            db.add(Role(id=role_id, name=name, description=description, permissions=permissions, builtin=True))
        else:
            role.name, role.description, role.permissions, role.builtin = name, description, permissions, True


def seed(db) -> None:
    sync_roles(db)
    db.flush()
    if not options.get(db, "seeded"):
        _seed(db)
        options.put(db, "seeded", True)
    if db.get(BotState, "support") is None:
        db.add(BotState(id="support"))
    db.commit()


def _seed(db) -> None:
    if not db.scalar(select(func.count()).select_from(Language)):
        default = normalize(settings().default_language) if normalize(settings().default_language) in KNOWN else "en"
        for position, code in enumerate(dict.fromkeys((default, *LANGUAGES))):
            name, native, flag, rtl = KNOWN[code]
            db.add(Language(code=code, name=name, native_name=native, flag=flag, rtl=rtl, enabled=True,
                            is_default=code == default, position=position))
    if not db.scalar(select(func.count()).select_from(TicketStatus)):
        for position, (key, stage, color, notify, names) in enumerate(STATUSES):
            db.add(TicketStatus(key=key, stage=stage, color=color, notify_user=notify, name=names, position=position, builtin=True))
        db.flush()
        for source, targets in TRANSITIONS.items():
            for target in targets:
                db.add(StatusTransition(from_key=source, to_key=target))
    if not db.scalar(select(func.count()).select_from(SlaPolicy)):
        for position, (priority, response, resolution) in enumerate(SLA):
            db.add(SlaPolicy(priority=priority, first_response_minutes=response, resolution_minutes=resolution, position=position))
    if not db.scalar(select(func.count()).select_from(Department)):
        for position, (name, categories) in enumerate(DEPARTMENTS):
            department = Department(name=name, position=position)
            db.add(department)
            db.flush()
            for index, (category, priority) in enumerate(categories):
                db.add(Category(department_id=department.id, name=category, default_priority=priority, position=index))
    if not db.scalar(select(func.count()).select_from(MenuItem)):
        for position, action in enumerate(MENU):
            db.add(MenuItem(action=action, label={}, position=position))
        # «My contacts» is ready but off: whether to ask users for a phone or e-mail is the operator's choice
        db.add(MenuItem(action="contacts", label={}, position=len(MENU), enabled=False))
    if not db.scalar(select(func.count()).select_from(FaqNode)):
        _faq(db, None, FAQ)
        db.flush()
        faq.publish(db, "system", "Example questions")


def _faq(db, parent_id, items) -> None:
    for position, (title, content, children) in enumerate(items):
        node = FaqNode(parent_id=parent_id, position=position, title=title, content=content or {}, updated_by="system")
        db.add(node)
        db.flush()
        _faq(db, node.id, children)
