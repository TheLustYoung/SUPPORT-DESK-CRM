"""Acceptance criteria 1, 2, 7 and 11: welcome message and media without code changes; nested FAQ branches
created, edited, deleted, translated and reordered; multilingual texts; a new language without code changes."""
import io
from backend.models import FaqNode
from backend.services.i18n import builtin, builtin_keys

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_faq_branch_lifecycle_and_publishing(admin, bot, db):
    root = admin.post("/api/admin/faq/nodes", json={"title": {"en": "Delivery", "ru": "Доставка"}}).json()
    child = admin.post("/api/admin/faq/nodes", json={"parent_id": root["id"], "title": {"en": "Where is my order?"},
                                                     "content": {"blocks": [{"type": "text", "text": {"en": "Track it <b>here</b>"}}]}}).json()
    assert admin.get("/api/admin/faq").json()["unpublished"] is True
    published_before = bot.get("/internal/content").json()["faq"]["nodes"]
    assert str(child["id"]) not in published_before  # users never see drafts
    assert admin.patch(f"/api/admin/faq/nodes/{child['id']}", json={"title": {"en": "Where is my order?", "ru": "Где мой заказ?"}}).status_code == 200
    assert admin.post(f"/api/admin/faq/nodes/{root['id']}/move", json={"parent_id": child["id"], "position": 0}).json()["detail"] == "move_into_itself"
    assert admin.post("/api/admin/faq/publish", json={"note": "delivery"}).status_code == 200
    bundle = bot.get("/internal/content").json()
    assert bundle["faq"]["nodes"][str(child["id"])]["title"]["ru"] == "Где мой заказ?"
    assert child["id"] in bundle["faq"]["children"][str(root["id"])]
    versions = admin.get("/api/admin/faq/versions").json()
    assert admin.delete(f"/api/admin/faq/nodes/{root['id']}").json()["removed"] == 2  # with its branch
    assert admin.post(f"/api/admin/faq/versions/{versions[0]['id']}/restore").status_code == 200
    db.expire_all()
    assert db.get(FaqNode, child["id"]).parent_id == root["id"]  # ids and relations come back


def test_faq_content_is_checked(admin):
    bad_html = admin.post("/api/admin/faq/nodes", json={"title": {"en": "X"}, "content": {"blocks": [{"type": "text", "text": {"en": "<script>"}}]}})
    assert bad_html.json()["detail"] == "bad_html"
    album = admin.post("/api/admin/faq/nodes", json={"title": {"en": "X"}, "content": {"blocks": [{"type": "album", "assets": [1]}]}})
    assert album.json()["detail"] == "album_size"


def test_welcome_with_media_and_menu(admin, bot):
    upload = admin.post("/api/admin/media", files={"file": ("banner.png", io.BytesIO(PNG), "image/png")})
    assert upload.status_code == 200 and upload.json()["kind"] == "photo"
    fake = admin.post("/api/admin/media", files={"file": ("virus.exe", io.BytesIO(b"MZ\x90\x00" * 10), "image/png")})
    assert fake.json()["detail"] == "unsupported_file"  # checked by content, not by name
    welcome = {"blocks": [{"type": "photo", "assets": [upload.json()["id"]], "text": {"en": "Hi!", "ar": "مرحبا"}}],
               "buttons": [{"label": {"en": "Website"}, "action": "url", "value": "https://example.com"}]}
    assert admin.put("/api/admin/content/welcome", json=welcome).status_code == 200
    menu = admin.get("/api/admin/content/menu").json()
    assert [(m["action"], m["enabled"]) for m in menu][-1] == ("contacts", False)  # ready, but asking for contacts is opt-in
    menu.reverse()
    menu[1]["enabled"] = False  # «Language»
    menu.append({"action": "url", "value": "https://example.com/help", "label": {"en": "Help centre"}})
    assert len(admin.put("/api/admin/content/menu", json=menu).json()) == 6
    bundle = bot.get("/internal/content").json()
    assert bundle["welcome"]["blocks"][0]["assets"] == [upload.json()["id"]]
    assert [m["action"] for m in bundle["menu"]] == ["tickets", "support", "faq", "url"]  # disabled item hidden, order kept
    asset = bot.get(f"/internal/assets/{upload.json()['id']}")
    assert asset.content == PNG and asset.headers["x-kind"] == "photo"
    assert admin.delete(f"/api/admin/media/{upload.json()['id']}").json()["detail"] == "media_in_use"


def test_new_language_and_texts_without_code(admin, bot):
    assert admin.post("/api/admin/settings/languages", json={"code": "fa"}).status_code == 200
    assert {"code": "fa", "native_name": "فارسی", "flag": "🇮🇷", "rtl": True} in bot.get("/internal/content").json()["languages"]
    saved = admin.put("/api/admin/content/texts/fa", json={"menu_title": "🏠 منوی اصلی"})
    assert saved.status_code == 200
    assert admin.put("/api/admin/content/texts/fa", json={"welcome": "{oops}"}).json()["detail"] == "bad_placeholder"
    bot.post("/internal/register", json={"user_id": 7, "first_name": "Sami", "language_code": "fa"})
    assert bot.post("/internal/register", json={"user_id": 7}).json()["language"] == "fa"  # Telegram language detected
    assert ["fa", "menu_title", "🏠 منوی اصلی"] in bot.get("/internal/content").json()["texts"]


def test_every_text_key_exists_in_english():
    """Translation keys are never shown to users: English has every key, other catalogs only known ones."""
    english = set(builtin_keys())
    for code, values in builtin().items():
        assert {k for k in values if not k.startswith("_")} <= english, code


def test_settings_validation(admin):
    assert admin.patch("/api/admin/settings/general", json={"reopen_days": 9999}).json()["detail"] == "bad_setting:reopen_days"
    hours = {"enabled": True, "timezone": "Europe/Moscow", "days": {"mon": ["09:00", "18:00"]}}
    assert admin.patch("/api/admin/settings/general", json={"business_hours": hours}).json()["business_hours"]["timezone"] == "Europe/Moscow"
    statuses = admin.post("/api/admin/settings/statuses", json={"key": "waiting_vendor", "stage": "pending_agent",
                                                                "name": {"en": "Waiting for vendor"}, "color": "#123456"})
    assert any(s["key"] == "waiting_vendor" for s in statuses.json())
    matrix = admin.get("/api/admin/settings/transitions").json()
    matrix["open"].append("waiting_vendor")
    assert "waiting_vendor" in admin.put("/api/admin/settings/transitions", json=matrix).json()["open"]
