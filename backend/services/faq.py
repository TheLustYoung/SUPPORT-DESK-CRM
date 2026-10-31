"""The FAQ tree: questions nested in categories, edited as a draft and published to the bot.

- Staff edit the draft (faq_nodes). Users only ever see a published snapshot, so half-finished edits never reach them;
  «Publish» takes a new snapshot, «Restore» brings an older version back into the draft. The dashboard previews the
  draft in a bot simulator and can send it to the editor's own Telegram before publishing.
- A node's place is (parent, position); moving keeps its whole branch and refuses moves into its own branch, so the
  parent–child relations always stay a tree.
- Practical limits (Settings → General) keep trees fast: depth, children per question and total questions.
"""
from sqlalchemy import delete, func, select, text
from backend.models import FaqNode, FaqVersion, now
from backend.services import options, rich, search
from backend.services.i18n import pick
from backend.services.security import DomainError

REDIRECTS = {"", "node", "url", "support", "menu"}
FIELDS = ("title", "content", "keywords", "enabled", "redirect", "redirect_value", "allow_ticket", "department_id", "category_id")


def node_dict(node: FaqNode) -> dict:
    return {"id": node.id, "parent_id": node.parent_id, "position": node.position, "enabled": node.enabled, "title": node.title or {},
            "content": node.content or {}, "keywords": node.keywords or {}, "redirect": node.redirect,
            "redirect_value": node.redirect_value, "allow_ticket": node.allow_ticket, "department_id": node.department_id,
            "category_id": node.category_id, "updated_at": node.updated_at.isoformat() if node.updated_at else None,
            "updated_by": node.updated_by}


def nodes(db) -> list[FaqNode]:
    return list(db.scalars(select(FaqNode).order_by(FaqNode.parent_id, FaqNode.position, FaqNode.id)))


def _children(all_nodes) -> dict:
    result: dict = {}
    for node in all_nodes:
        result.setdefault(node.parent_id, []).append(node)
    for siblings in result.values():
        siblings.sort(key=lambda n: (n.position, n.id))
    return result


def _depth(by_id: dict, node_id: int | None) -> int:
    depth = 0
    while node_id is not None:
        depth += 1
        node_id = by_id[node_id].parent_id
    return depth


def _branch(children: dict, node_id: int) -> list[int]:
    found, stack = [], [node_id]
    while stack:
        current = stack.pop()
        found.append(current)
        stack.extend(n.id for n in children.get(current, []))
    return found


def _height(children: dict, node_id: int) -> int:
    return 1 + max((_height(children, k.id) for k in children.get(node_id, [])), default=0)


def _limits(db) -> tuple[int, int, int]:
    return int(options.get(db, "faq_max_depth")), int(options.get(db, "faq_max_children")), int(options.get(db, "faq_max_nodes"))


def clean(db, data: dict, partial: bool = False) -> dict:
    """Checks a node's fields; raises DomainError with a code the dashboard translates."""
    result = {}
    if "title" in data or not partial:
        title = {str(k)[:12]: str(v).strip()[:64] for k, v in (data.get("title") or {}).items() if str(v or "").strip()}
        if not title:
            raise DomainError("title_required")
        result["title"] = title
    if "content" in data:
        try:
            content = rich.parse(data.get("content"))
            rich.check_assets(db, content)
        except ValueError as error:
            raise DomainError(rich.reason(error)) from None
        result["content"] = content.model_dump()
    if "keywords" in data:
        result["keywords"] = {str(k)[:12]: str(v).strip()[:500] for k, v in (data.get("keywords") or {}).items() if str(v or "").strip()}
    if "redirect" in data:
        redirect, value = str(data.get("redirect") or ""), str(data.get("redirect_value") or "").strip()
        if redirect not in REDIRECTS:
            raise DomainError("bad_redirect")
        if redirect == "url" and not value.startswith(("https://", "http://")):
            raise DomainError("bad_url")
        if redirect == "node" and (not value.isdigit() or db.get(FaqNode, int(value)) is None):
            raise DomainError("bad_redirect")
        result["redirect"], result["redirect_value"] = redirect, value if redirect in ("url", "node") else ""
    for key in ("enabled", "allow_ticket"):
        if key in data:
            result[key] = bool(data[key])
    for key in ("department_id", "category_id"):
        if key in data:
            result[key] = int(data[key]) if data.get(key) not in (None, "", 0) else None
    return result


def create(db, parent_id: int | None, data: dict, actor: str) -> FaqNode:
    all_nodes = nodes(db)
    by_id, children = {n.id: n for n in all_nodes}, _children(all_nodes)
    max_depth, max_children, max_nodes = _limits(db)
    if parent_id is not None and parent_id not in by_id:
        raise DomainError("not_found", 404)
    if len(all_nodes) >= max_nodes:
        raise DomainError("too_many_nodes")
    if len(children.get(parent_id, [])) >= max_children:
        raise DomainError("too_many_children")
    if _depth(by_id, parent_id) + 1 > max_depth:
        raise DomainError("too_deep")
    node = FaqNode(parent_id=parent_id, position=len(children.get(parent_id, [])), updated_by=actor, updated_at=now(), **clean(db, data))
    db.add(node)
    db.flush()
    return node


def update(db, node_id: int, data: dict, actor: str) -> FaqNode:
    node = db.get(FaqNode, node_id)
    if node is None:
        raise DomainError("not_found", 404)
    for key, value in clean(db, data, partial=True).items():
        setattr(node, key, value)
    if node.redirect == "node" and node.redirect_value == str(node.id):
        raise DomainError("bad_redirect")
    node.updated_by, node.updated_at = actor, now()
    return node


def move(db, node_id: int, parent_id: int | None, position: int, actor: str) -> None:
    """Moves a question with its whole branch under another parent (None = top level), at the given place."""
    all_nodes = nodes(db)
    by_id, children = {n.id: n for n in all_nodes}, _children(all_nodes)
    node = by_id.get(node_id)
    if node is None or (parent_id is not None and parent_id not in by_id):
        raise DomainError("not_found", 404)
    if parent_id is not None and parent_id in _branch(children, node_id):
        raise DomainError("move_into_itself")
    max_depth, max_children, _ = _limits(db)
    if _depth(by_id, parent_id) + _height(children, node_id) > max_depth:
        raise DomainError("too_deep")
    siblings = [n for n in children.get(parent_id, []) if n.id != node_id]
    if parent_id != node.parent_id and len(siblings) >= max_children:
        raise DomainError("too_many_children")
    old_parent = node.parent_id
    siblings.insert(max(0, min(position, len(siblings))), node)
    node.parent_id, node.updated_by, node.updated_at = parent_id, actor, now()
    for index, item in enumerate(siblings):
        item.position = index
    if old_parent != parent_id:  # close the gap where it was
        for index, item in enumerate(n for n in children.get(old_parent, []) if n.id != node_id):
            item.position = index


def reorder(db, parent_id: int | None, ordered: list[int]) -> None:
    current = [n for n in nodes(db) if n.parent_id == parent_id]
    if sorted(n.id for n in current) != sorted(ordered):
        raise DomainError("bad_order")
    position = {node_id: index for index, node_id in enumerate(ordered)}
    for node in current:
        node.position = position[node.id]


def remove(db, node_id: int) -> int:
    """Deletes a question with its whole branch; returns how many questions were removed."""
    all_nodes = nodes(db)
    if node_id not in {n.id for n in all_nodes}:
        raise DomainError("not_found", 404)
    branch = _branch(_children(all_nodes), node_id)
    db.execute(delete(FaqNode).where(FaqNode.id.in_(branch)))
    return len(branch)


# ---------------------------------------------------------------- publishing
def snapshot_of(all_nodes) -> dict:
    """What users see: enabled questions whose parents are all enabled, in order."""
    children = _children(all_nodes)
    visible: dict[str, dict] = {}
    order: dict[str, list[int]] = {}

    def walk(parent_id):
        kept = []
        for node in children.get(parent_id, []):
            if not node.enabled:
                continue
            data = node_dict(node)
            for key in ("updated_at", "updated_by", "enabled", "position"):
                data.pop(key)
            visible[str(node.id)] = data
            kept.append(node.id)
            walk(node.id)
        order["root" if parent_id is None else str(parent_id)] = kept
    walk(None)
    return {"nodes": visible, "children": order}


def publish(db, actor: str, note: str = "") -> FaqVersion:
    version = FaqVersion(snapshot=snapshot_of(nodes(db)), note=note[:255], published_by=actor, published_at=now())
    db.add(version)
    options.bump_content_version(db)
    db.flush()
    return version


def latest(db) -> FaqVersion | None:
    return db.scalar(select(FaqVersion).order_by(FaqVersion.id.desc()).limit(1))


def has_unpublished(db) -> bool:
    current = latest(db)
    return current is None or current.snapshot != snapshot_of(nodes(db))


def restore(db, version_id: int, actor: str) -> None:
    """Replaces the draft with an older published version (question IDs are kept, so reports stay linked)."""
    version = db.get(FaqVersion, version_id)
    if version is None:
        raise DomainError("not_found", 404)
    snapshot = version.snapshot
    db.execute(delete(FaqNode))
    db.flush()
    db.expunge_all()
    position_of = {int(i): p for _, ids in snapshot["children"].items() for p, i in enumerate(ids)}
    pending = dict(snapshot["nodes"])
    while pending:  # parents first
        ready = [k for k, n in pending.items() if n["parent_id"] is None or str(n["parent_id"]) not in pending]
        for key in ready:
            item = pending.pop(key)
            db.add(FaqNode(id=item["id"], parent_id=item["parent_id"], position=position_of.get(item["id"], 0), enabled=True,
                           updated_by=actor, updated_at=now(), **{f: item[f] for f in FIELDS if f != "enabled" and f in item}))
        db.flush()
    if db.bind.dialect.name == "postgresql":  # keep the id sequence ahead of restored ids
        db.execute(text("SELECT setval(pg_get_serial_sequence('faq_nodes', 'id'), COALESCE((SELECT MAX(id) FROM faq_nodes), 1))"))


def count(db) -> int:
    return db.scalar(select(func.count()).select_from(FaqNode))


# ---------------------------------------------------------------- reading a published tree (bot side)
class Tree:
    def __init__(self, snapshot: dict | None):
        snapshot = snapshot or {"nodes": {}, "children": {}}
        self.nodes = {int(k): v for k, v in snapshot["nodes"].items()}
        self.kids = {(None if k == "root" else int(k)): v for k, v in snapshot["children"].items()}

    def children(self, node_id: int | None) -> list[dict]:
        return [self.nodes[i] for i in self.kids.get(node_id, []) if i in self.nodes]

    def get(self, node_id: int) -> dict | None:
        return self.nodes.get(node_id)

    def path(self, node_id: int) -> list[int]:
        result = []
        while node_id is not None and node_id in self.nodes and len(result) < 50:
            result.append(node_id)
            node_id = self.nodes[node_id]["parent_id"]
        return list(reversed(result))

    def search(self, query: str, lang: str, default: str, limit: int = 3) -> list[dict]:
        items = []
        for node in self.nodes.values():
            body = " ".join(pick(b.get("text"), lang, default) for b in (node["content"] or {}).get("blocks", []))
            if not body and self.kids.get(node["id"]):
                continue  # categories are found through their questions
            items.append((node["id"], pick(node["title"], lang, default), pick(node["keywords"], lang, default), body))
        return [self.nodes[i] for i, _ in search.rank(query, items, limit)]
