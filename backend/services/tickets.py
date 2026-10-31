"""Tickets: creation, conversation, statuses with permitted transitions, priorities, departments,
assignment, internal notes, SLA targets and the history of every change.

Who changes what is an Actor: a staff member (checked against the configured transitions), the user (their own
follow-up actions in the bot) or the system. Automatic moves (documented for administrators in docs/TICKETS.md):
- the user answers a ticket that waits for them (stage pending_user) → the first «pending agent» status;
- the user writes in a resolved ticket → «reopened»;
- an agent's first reply to a new, reopened or «pending agent» ticket → «open»;
- resolved tickets close by themselves after the configured time (Settings → General).
Outgoing messages are stored first (delivery «queued») and sent by the workers after the commit.
"""
from dataclasses import dataclass
from datetime import timedelta
from sqlalchemy import and_, false, func, or_, select, true
from sqlalchemy.exc import IntegrityError
from backend.models import (AssignmentRule, Category, Department, Message, SlaPolicy, Staff, StaffDepartment, StatusTransition,
                            Ticket, TicketEvent, TicketStatus, TgUser, UserEvent, now)
from backend.services import audit, notify, options, queue
from backend.services.i18n import pick
from backend.services.security import DomainError

PRIORITIES = ("low", "normal", "high", "urgent")
STAGES = ("new", "open", "pending_agent", "pending_user", "escalated", "resolved", "closed", "reopened")
FINAL = {"resolved", "closed"}
SEND_TASK = "workers.tasks.send_message"


@dataclass
class Actor:
    type: str  # staff, user, system
    id: int | None = None
    name: str = "system"
    staff: Staff | None = None
    ip: str = ""

    @classmethod
    def of(cls, staff: Staff, ip: str = "") -> "Actor":
        return cls("staff", staff.id, staff.username, staff, ip)

    @classmethod
    def user(cls, user: TgUser) -> "Actor":
        return cls("user", user.id, user.full_name[:64])


SYSTEM = Actor("system")


# ---------------------------------------------------------------- statuses
def statuses(db) -> dict[str, TicketStatus]:
    return {s.key: s for s in db.scalars(select(TicketStatus).order_by(TicketStatus.position))}


def status_for(db, stage: str) -> str | None:
    """The first enabled status of a stage (the built-in one unless an administrator reordered them)."""
    return next((s.key for s in statuses(db).values() if s.stage == stage and s.enabled), None)


def stage_of(db, key: str) -> str:
    status = db.get(TicketStatus, key)
    return status.stage if status else "open"


def keys_of(db, stages: set[str]) -> list[str]:
    return [k for k, s in statuses(db).items() if s.stage in stages]


def transitions(db) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for row in db.scalars(select(StatusTransition)):
        result.setdefault(row.from_key, set()).add(row.to_key)
    return result


# ---------------------------------------------------------------- creation
def number_for(ticket_id: int, prefix: str) -> str:
    return f"{prefix}{ticket_id:06d}"


def apply_sla(db, ticket: Ticket) -> None:
    """Targets count from the ticket's creation (Settings → Business hours and SLA): in business hours or calendar time,
    plus the time the ticket spent waiting for the user when that pause is on. A new priority recalculates them."""
    policy = db.get(SlaPolicy, ticket.priority)
    if policy is None:
        ticket.due_response_at = ticket.due_resolution_at = None
        return
    hours, paused = options.sla_hours(db), ticket.sla_paused_seconds or 0
    ticket.due_response_at = options.add_working_seconds(ticket.created_at, policy.first_response_minutes * 60 + paused, hours)
    ticket.due_resolution_at = options.add_working_seconds(ticket.created_at, policy.resolution_minutes * 60 + paused, hours)


def _pause(db, ticket: Ticket, old_stage: str, new_stage: str, moment) -> None:
    """While a ticket waits for the user, SLA time stops (when Settings → «Pause SLA» is on); the waited time is
    added to the targets when the ticket moves on."""
    if new_stage == "pending_user" and old_stage != "pending_user":
        if options.get(db, "sla_pause_pending_user"):
            ticket.sla_paused_at = moment
    elif old_stage == "pending_user" and ticket.sla_paused_at:
        ticket.sla_paused_seconds = (ticket.sla_paused_seconds or 0) + int(
            options.working_seconds(ticket.sla_paused_at, moment, options.sla_hours(db)))
        ticket.sla_paused_at = None
        apply_sla(db, ticket)


def agents_of(db, department_id: int | None) -> list[Staff]:
    query = select(Staff).where(Staff.active.is_(True))
    if department_id is not None:
        query = query.join(StaffDepartment, StaffDepartment.staff_id == Staff.id).where(StaffDepartment.department_id == department_id)
    return [s for s in db.scalars(query).unique() if "tickets.reply" in s.permissions]


def open_load(db, staff_ids: list[int]) -> dict[int, int]:
    if not staff_ids:
        return {}
    rows = db.execute(select(Ticket.assignee_id, func.count()).where(Ticket.assignee_id.in_(staff_ids), Ticket.status.notin_(keys_of(db, FINAL)))
                      .group_by(Ticket.assignee_id))
    return {staff_id: count for staff_id, count in rows}


def pick_agent(db, department_id: int | None, language: str, strategy: str) -> int | None:
    """Least loaded (fewest open tickets) or round robin among the department's agents; agents speaking the ticket's
    language come first; agents at their open-ticket limit are skipped."""
    agents = agents_of(db, department_id)
    load = open_load(db, [a.id for a in agents])
    agents = [a for a in agents if not a.max_open_tickets or load.get(a.id, 0) < a.max_open_tickets]
    agents = [a for a in agents if language in (a.languages or [])] or agents
    if not agents:
        return None
    if strategy == "round_robin":
        key = f"round_robin:{department_id}"
        last = options.get(db, key)
        ordered = sorted(a.id for a in agents)
        chosen = next((i for i in ordered if last is None or i > last), ordered[0])
        options.put(db, key, chosen)
        return chosen
    return min(agents, key=lambda a: (load.get(a.id, 0), a.id)).id


def route(db, ticket: Ticket) -> None:
    """Applies the first matching assignment rule (Settings → Routing)."""
    rules = db.scalars(select(AssignmentRule).where(AssignmentRule.enabled.is_(True)).order_by(AssignmentRule.position, AssignmentRule.id))
    for rule in rules:
        if rule.department_id is not None and rule.department_id != ticket.department_id:
            continue
        if rule.category_id is not None and rule.category_id != ticket.category_id:
            continue
        if rule.language and rule.language != ticket.language:
            continue
        if rule.priority and rule.priority != ticket.priority:
            continue
        if rule.set_department_id:
            ticket.department_id = rule.set_department_id
        if rule.set_priority in PRIORITIES:
            ticket.priority = rule.set_priority
        if rule.assign_to:
            agent = db.get(Staff, rule.assign_to)
            if agent and agent.active:
                ticket.assignee_id = agent.id
        elif rule.strategy in ("least_loaded", "round_robin"):
            ticket.assignee_id = pick_agent(db, ticket.department_id, ticket.language, rule.strategy)
        return


def create(db, user: TgUser, *, language: str, department_id: int | None = None, category_id: int | None = None,
           subject: str = "", source: str = "bot", faq_node_id: int | None = None, dedupe_key: str | None = None,
           actor: Actor | None = None, priority: str | None = None) -> tuple[Ticket, bool]:
    """A new ticket; (ticket, False) when the dedupe key shows it was already created (a retried update)."""
    if dedupe_key:
        existing = db.scalar(select(Ticket).where(Ticket.dedupe_key == dedupe_key))
        if existing:
            return existing, False
    category = db.get(Category, category_id) if category_id else None
    if category_id and (category is None or not category.enabled):
        raise DomainError("unknown_category")
    if category and department_id is None:
        department_id = category.department_id
    if department_id is not None and db.get(Department, department_id) is None:
        raise DomainError("unknown_department")
    moment = now()
    ticket = Ticket(user_id=user.id, department_id=department_id, category_id=category_id, language=language,
                    priority=priority if priority in PRIORITIES else (category.default_priority if category else "normal"),
                    status=status_for(db, "new") or "new", subject=(subject or "").strip()[:200], source=source,
                    faq_node_id=faq_node_id, dedupe_key=dedupe_key, created_at=moment, updated_at=moment,
                    bot_id=user.bot_id or "main")  # the ticket is answered through the bot the user talks to
    try:
        with db.begin_nested():
            db.add(ticket)
            db.flush()
    except IntegrityError:  # the same update handled twice at once
        return db.scalar(select(Ticket).where(Ticket.dedupe_key == dedupe_key)), False
    ticket.number = number_for(ticket.id, str(options.get(db, "ticket_prefix") or ""))
    route(db, ticket)
    apply_sla(db, ticket)
    actor = actor or Actor.user(user)
    db.add(TicketEvent(ticket_id=ticket.id, kind="created", new=ticket.status, actor_type=actor.type, actor_id=actor.id, actor_name=actor.name))
    if ticket.assignee_id:
        db.add(TicketEvent(ticket_id=ticket.id, kind="assignee", new=str(ticket.assignee_id), actor_type="system", actor_name="routing"))
    db.add(UserEvent(user_id=user.id, kind="ticket_created", ref_id=ticket.id, data={"faq_node": faq_node_id, "source": source}))
    db.flush()
    people = notify.audience(db, ticket)
    about = {"number": ticket.number, "user": user.full_name, "subject": ticket.subject}
    if ticket.assignee_id:
        notify.send(db, [p for p in people if p.id == ticket.assignee_id], "assigned", ticket, about)
    else:
        notify.send(db, people, "new_ticket", ticket, about, exclude=actor.id if actor.type == "staff" else None)
    return ticket, True


# ---------------------------------------------------------------- conversation
def add_incoming(db, ticket: Ticket, user: TgUser, *, kind: str, text: str | None, file_id: str | None = None,
                 file_name: str | None = None, file_size: int | None = None, tg_message_id: int | None = None,
                 dedupe_key: str | None = None, notify_staff: bool = True) -> Message | None:
    """A user's message in their ticket; None when this Telegram message was already stored (retried update)."""
    message = Message(user_id=user.id, ticket_id=ticket.id, direction="in", author=user.full_name[:64], kind=kind, text=text,
                      file_id=file_id, file_name=file_name, file_size=file_size, tg_message_id=tg_message_id, dedupe_key=dedupe_key)
    try:
        with db.begin_nested():
            db.add(message)
            db.flush()
    except IntegrityError:
        return None
    moment = now()
    ticket.last_user_message_at = ticket.updated_at = moment
    if not ticket.subject and text:
        ticket.subject = text.strip().splitlines()[0][:200]
    stage = stage_of(db, ticket.status)
    if stage == "pending_user":
        target = status_for(db, "pending_agent")
        if target:
            change_status(db, ticket, target, Actor.user(user), force=True, notify_user=False, comment="user replied")
    elif stage == "resolved":
        target = status_for(db, "reopened")
        if target:
            change_status(db, ticket, target, Actor.user(user), force=True, notify_user=False, comment="user wrote again")
    if file_id:
        queue.later(db, "workers.tasks.copy_attachment", message.id)  # a copy in our storage: Telegram files may expire
    if notify_staff:
        notify.send(db, notify.audience(db, ticket), "reply", ticket,
                    {"number": ticket.number, "user": user.full_name, "text": (text or f"[{kind}]")[:200]})
    return message


def outgoing(db, user_id: int, ticket: Ticket | None, staff: Staff | None, text: str, *, asset_id: int | None = None,
             kind: str = "text", file_name: str | None = None, direction: str = "out", markup: str = "",
             file_id: str | None = None, dedupe_key: str | None = None) -> Message:
    """Stores a message to a user and sends it through the workers after the commit."""
    message = Message(user_id=user_id, ticket_id=ticket.id if ticket else None, direction=direction, staff_id=staff.id if staff else None,
                      author=(staff.name if staff else "system")[:64], kind=kind, text=text or None, asset_id=asset_id,
                      file_id=file_id, file_name=file_name, markup=markup, delivery="queued", created_at=now(), dedupe_key=dedupe_key)
    db.add(message)
    db.flush()
    queue.later(db, SEND_TASK, message.id)
    if file_id:
        queue.later(db, "workers.tasks.copy_attachment", message.id)  # the history keeps the file after Telegram drops it
    return message


def already_stored(db, dedupe_key: str | None) -> bool:
    """A Telegram update the bot sent twice (retry, restart): its message is in the history already."""
    return bool(dedupe_key) and db.scalar(select(Message.id).where(Message.dedupe_key == dedupe_key)) is not None


def check_text(text: str, with_file: bool) -> str:
    text = (text or "").strip()
    if not text and not with_file:
        raise DomainError("empty_message")
    if len(text) > (1024 if with_file else 4096):
        raise DomainError("message_too_long")
    return text


def reply(db, ticket: Ticket, staff: Staff, text: str, *, asset_id: int | None = None, kind: str = "text",
          file_name: str | None = None, file_id: str | None = None, dedupe_key: str | None = None) -> Message:
    """An agent's answer — from the dashboard or from Telegram — delivered to the user by the workers (retried; the
    delivery status is kept)."""
    text = check_text(text, bool(asset_id or file_id))
    if stage_of(db, ticket.status) == "closed":
        raise DomainError("ticket_closed")
    message = outgoing(db, ticket.user_id, ticket, staff, text, asset_id=asset_id, kind=kind, file_name=file_name,
                       file_id=file_id, dedupe_key=dedupe_key)
    moment = message.created_at
    if ticket.first_response_at is None:
        ticket.first_response_at = moment
        if ticket.due_response_at and moment > ticket.due_response_at:
            ticket.response_breached = True
    ticket.last_staff_message_at = ticket.updated_at = moment
    if ticket.assignee_id is None and "tickets.reply" in staff.permissions:
        assign(db, ticket, staff.id, Actor.of(staff), quiet=True)  # whoever answers first takes the ticket
    if stage_of(db, ticket.status) in {"new", "pending_agent", "reopened"}:
        target = status_for(db, "open")
        if target and target != ticket.status:
            change_status(db, ticket, target, Actor.of(staff), force=True, notify_user=False)
    return message


def note(db, ticket: Ticket, staff: Staff, text: str, dedupe_key: str | None = None) -> Message:
    """An internal note: visible to staff with tickets.notes only, never sent to the user."""
    text = (text or "").strip()
    if not text or len(text) > 4000:
        raise DomainError("empty_message")
    message = Message(user_id=ticket.user_id, ticket_id=ticket.id, direction="note", staff_id=staff.id, author=staff.name[:64],
                      kind="text", text=text, dedupe_key=dedupe_key)
    db.add(message)
    ticket.updated_at = now()
    db.flush()
    return message


# ---------------------------------------------------------------- changes
def _event(db, ticket, kind, old, new, actor: Actor, comment=""):
    db.add(TicketEvent(ticket_id=ticket.id, kind=kind, old=None if old is None else str(old), new=None if new is None else str(new),
                       actor_type=actor.type, actor_id=actor.id, actor_name=actor.name, comment=comment[:500]))
    if actor.type == "staff":
        audit.record(db, actor.staff, f"ticket.{kind}", "ticket", ticket.number or ticket.id, {"from": old, "to": new}, actor.ip)


def change_status(db, ticket: Ticket, to: str, actor: Actor, *, force: bool = False, notify_user: bool = True, comment: str = "") -> None:
    every = statuses(db)
    target = every.get(to)
    if target is None or not target.enabled:
        raise DomainError("unknown_status")
    if to == ticket.status:
        return
    if not force and to not in transitions(db).get(ticket.status, set()):
        raise DomainError("transition_not_allowed")
    old_stage = every[ticket.status].stage if ticket.status in every else "open"
    if actor.type == "staff" and old_stage in FINAL and target.stage not in FINAL and "tickets.reopen" not in actor.staff.permissions:
        raise DomainError("no_permission", 403)
    old, moment = ticket.status, now()
    ticket.status, ticket.updated_at, ticket.reminded_at = to, moment, None  # a new wait gets its own reminder
    _pause(db, ticket, old_stage, target.stage, moment)
    if target.stage == "resolved":
        ticket.resolved_at = moment
    elif target.stage == "closed":
        ticket.closed_at = moment
        ticket.resolved_at = ticket.resolved_at or moment
    elif old_stage in FINAL:
        ticket.reopen_count += 1
        ticket.resolved_at = ticket.closed_at = None
    _event(db, ticket, "status", old, to, actor, comment)
    if target.stage == "escalated":
        notify.send(db, notify.audience(db, ticket, supervisors=True), "escalated", ticket, {"number": ticket.number, "subject": ticket.subject},
                    exclude=actor.id if actor.type == "staff" else None)
    if notify_user and target.notify_user and actor.type != "user":
        _tell_user(db, ticket, target)


def _tell_user(db, ticket: Ticket, status: TicketStatus) -> None:
    from backend.services.content import translator
    t = translator(db)
    lang = ticket.language
    name = pick(status.name, lang, t.default)
    markup = ""
    if status.stage == "pending_user":
        text = t(lang, "status_pending_user", number=ticket.number)
    elif status.stage == "resolved":
        rating = bool(options.get(db, "csat_enabled")) and ticket.rating is None
        text, markup = t(lang, "status_resolved" if rating else "status_changed", number=ticket.number, status=name), "rating" if rating else "reopen"
    elif status.stage == "closed":
        text = t(lang, "status_closed", number=ticket.number)
    else:
        text = t(lang, "status_changed", number=ticket.number, status=name)
    outgoing(db, ticket.user_id, ticket, None, text, direction="system", markup=markup)


def set_priority(db, ticket: Ticket, priority: str, actor: Actor) -> None:
    if priority not in PRIORITIES:
        raise DomainError("unknown_priority")
    if priority == ticket.priority:
        return
    old, ticket.priority, ticket.updated_at = ticket.priority, priority, now()
    apply_sla(db, ticket)
    _event(db, ticket, "priority", old, priority, actor)


def transfer(db, ticket: Ticket, department_id: int | None, actor: Actor, comment: str = "") -> None:
    """Moves the ticket to another department; its agent stays only if they belong to the new department."""
    if department_id is not None and db.get(Department, department_id) is None:
        raise DomainError("unknown_department")
    if department_id == ticket.department_id:
        return
    old, ticket.department_id, ticket.updated_at = ticket.department_id, department_id, now()
    category = db.get(Category, ticket.category_id) if ticket.category_id else None
    if category and category.department_id not in (None, department_id):
        ticket.category_id = None
    _event(db, ticket, "department", old, department_id, actor, comment)
    if ticket.assignee_id and department_id is not None and ticket.assignee_id not in notify.department_members(db, department_id):
        assign(db, ticket, None, actor)
    if ticket.assignee_id is None:
        notify.send(db, notify.audience(db, ticket), "transferred", ticket, {"number": ticket.number, "subject": ticket.subject},
                    exclude=actor.id if actor.type == "staff" else None)


def set_category(db, ticket: Ticket, category_id: int | None, actor: Actor) -> None:
    category = db.get(Category, category_id) if category_id else None
    if category_id and category is None:
        raise DomainError("unknown_category")
    if category_id == ticket.category_id:
        return
    old, ticket.category_id, ticket.updated_at = ticket.category_id, category_id, now()
    _event(db, ticket, "category", old, category_id, actor)


def assign(db, ticket: Ticket, staff_id: int | None, actor: Actor, quiet: bool = False) -> None:
    if staff_id is not None:
        agent = db.get(Staff, staff_id)
        if agent is None or not agent.active or "tickets.reply" not in agent.permissions:
            raise DomainError("unknown_agent")
    if staff_id == ticket.assignee_id:
        return
    old, ticket.assignee_id, ticket.updated_at = ticket.assignee_id, staff_id, now()
    _event(db, ticket, "assignee", old, staff_id, actor)
    if staff_id is not None and not quiet and not (actor.type == "staff" and actor.id == staff_id):
        notify.send(db, [db.get(Staff, staff_id)], "assigned", ticket, {"number": ticket.number, "subject": ticket.subject})


def rate(db, ticket: Ticket, user_id: int, stars: int, comment: str = "") -> bool:
    """The ticket's own user rates it once, after it is resolved or closed."""
    if ticket.user_id != user_id or ticket.rating is not None or not 1 <= stars <= 5:
        return False
    if stage_of(db, ticket.status) not in FINAL:
        return False
    ticket.rating, ticket.rating_comment = stars, comment[:500]
    db.add(TicketEvent(ticket_id=ticket.id, kind="rating", new=str(stars), actor_type="user", actor_id=user_id))
    return True


def user_close(db, ticket: Ticket, user: TgUser) -> bool:
    """«Problem solved» in the bot: the user closes their own ticket (also one the team marked resolved)."""
    if stage_of(db, ticket.status) == "closed":
        return False
    target = status_for(db, "closed")
    if not target:
        return False
    change_status(db, ticket, target, Actor.user(user), force=True, notify_user=False, comment="closed by the user")
    notify.send(db, notify.audience(db, ticket), "user_closed", ticket, {"number": ticket.number, "user": user.full_name})
    return True


def staff_close(db, ticket: Ticket, actor: Actor, comment: str = "") -> None:
    """«Close ticket» in the dashboard: from any status, the user is told."""
    target = status_for(db, "closed")
    if not target:
        raise DomainError("unknown_status")
    if ticket.status == target:
        return
    change_status(db, ticket, target, actor, force=True, comment=comment)


def staff_reopen(db, ticket: Ticket, actor: Actor, comment: str = "") -> None:
    if stage_of(db, ticket.status) not in FINAL:
        return
    target = status_for(db, "reopened") or status_for(db, "open")
    if not target:
        raise DomainError("unknown_status")
    change_status(db, ticket, target, actor, force=True, notify_user=False, comment=comment)


def user_reopen(db, ticket: Ticket, user: TgUser) -> str:
    """"Ok", "expired" (closed longer than the reopen window) or "not_allowed"."""
    stage = stage_of(db, ticket.status)
    if stage not in FINAL:
        return "not_allowed"
    days = int(options.get(db, "reopen_days") or 0)
    finished = ticket.closed_at or ticket.resolved_at or ticket.updated_at
    if stage == "closed" and (not days or now() - finished > timedelta(days=days)):
        return "expired"
    target = status_for(db, "reopened")
    if not target:
        return "not_allowed"
    change_status(db, ticket, target, Actor.user(user), force=True, notify_user=False, comment="reopened by the user")
    notify.send(db, notify.audience(db, ticket), "reopened", ticket, {"number": ticket.number, "user": user.full_name})
    return "ok"


# ---------------------------------------------------------------- scheduled checks
def check_sla(db) -> int:
    """Marks tickets that missed their first-response or resolution target and tells their agent and supervisors."""
    moment, final = now(), keys_of(db, FINAL)
    late_response = db.scalars(select(Ticket).where(Ticket.first_response_at.is_(None), Ticket.response_breached.is_(False),
                                                    Ticket.due_response_at < moment, Ticket.status.notin_(final), Ticket.sla_paused_at.is_(None))).all()
    late_resolution = db.scalars(select(Ticket).where(Ticket.resolution_breached.is_(False), Ticket.due_resolution_at < moment,
                                                      Ticket.status.notin_(final), Ticket.sla_paused_at.is_(None))).all()
    found = 0
    for ticket, what in [(t, "first_response") for t in late_response] + [(t, "resolution") for t in late_resolution]:
        if what == "first_response":
            ticket.response_breached = True
        else:
            ticket.resolution_breached = True
        db.add(TicketEvent(ticket_id=ticket.id, kind="sla", new=what, actor_type="system", actor_name="SLA"))
        notify.send(db, notify.audience(db, ticket, supervisors=True), "overdue", ticket, {"number": ticket.number, "what": what})
        found += 1
    return found


def _warned(db, ticket: Ticket, kind: str, what: str) -> bool:
    return db.scalar(select(func.count()).select_from(TicketEvent).where(TicketEvent.ticket_id == ticket.id, TicketEvent.kind == kind,
                                                                        TicketEvent.new == what)) > 0


def warn_sla(db) -> int:
    """Scheduled reminder for the team: once per target, when `sla_warning_percent` of its time has passed
    and the deadline is still ahead — so the agent can answer before the ticket becomes overdue."""
    percent = int(options.get(db, "sla_warning_percent") or 0)
    if not percent:
        return 0
    moment, final, sent = now(), keys_of(db, FINAL), 0
    rows = db.scalars(select(Ticket).where(Ticket.status.notin_(final), Ticket.sla_paused_at.is_(None),
                                           or_(and_(Ticket.first_response_at.is_(None), Ticket.response_breached.is_(False),
                                                    Ticket.due_response_at > moment),
                                               and_(Ticket.resolution_breached.is_(False), Ticket.due_resolution_at > moment)))).all()
    for ticket in rows:
        targets = []
        if ticket.first_response_at is None and not ticket.response_breached and ticket.due_response_at and ticket.due_response_at > moment:
            targets.append(("first_response", ticket.due_response_at))
        if not ticket.resolution_breached and ticket.due_resolution_at and ticket.due_resolution_at > moment:
            targets.append(("resolution", ticket.due_resolution_at))
        for what, due in targets:
            if moment < ticket.created_at + (due - ticket.created_at) * percent / 100 or _warned(db, ticket, "sla_warning", what):
                continue
            db.add(TicketEvent(ticket_id=ticket.id, kind="sla_warning", new=what, actor_type="system", actor_name="SLA"))
            minutes = max(1, int((due - moment).total_seconds() // 60))
            notify.send(db, notify.audience(db, ticket), "sla_warning", ticket, {"number": ticket.number, "what": what, "minutes": minutes})
            sent += 1
    return sent


def remind_users(db) -> int:
    """Scheduled reminder for users: a ticket waiting for the user's answer longer than `remind_user_hours`
    gets one reminder in the user's language; a new status or the user's reply resets it."""
    hours = int(options.get(db, "remind_user_hours") or 0)
    waiting = keys_of(db, {"pending_user"})
    if not hours or not waiting:
        return 0
    from backend.services import content
    t, moment, sent = content.translator(db), now(), 0
    for ticket in db.scalars(select(Ticket).where(Ticket.status.in_(waiting), Ticket.reminded_at.is_(None))).all():
        since = db.scalar(select(func.max(TicketEvent.created_at)).where(TicketEvent.ticket_id == ticket.id, TicketEvent.kind == "status"))
        since = max(filter(None, (since, ticket.last_staff_message_at, ticket.created_at)))
        if moment - since < timedelta(hours=hours):
            continue
        user = db.get(TgUser, ticket.user_id)
        if user is None or user.bot_blocked or user.anonymized or user.banned:
            ticket.reminded_at = moment  # nobody to remind: do not look at it again
            continue
        outgoing(db, user.id, ticket, None, t(ticket.language, "reminder_pending_user", number=ticket.number), direction="system")
        ticket.reminded_at = moment
        db.add(TicketEvent(ticket_id=ticket.id, kind="reminder", new="user", actor_type="system", actor_name="scheduler"))
        sent += 1
    return sent


def scheduled(db) -> dict:
    """Everything the scheduler does with tickets every minute."""
    return {"breached": check_sla(db), "warned": warn_sla(db), "reminded": remind_users(db), "closed": auto_close(db)}


def recalculate_sla(db) -> int:
    """New business hours or SLA settings apply to open tickets at once (breaches already recorded stay)."""
    rows = db.scalars(select(Ticket).where(Ticket.status.notin_(keys_of(db, FINAL)))).all()
    for ticket in rows:
        apply_sla(db, ticket)
    return len(rows)


def auto_close(db) -> int:
    hours, target = int(options.get(db, "auto_close_resolved_hours") or 0), status_for(db, "closed")
    if not hours or not target:
        return 0
    stale = db.scalars(select(Ticket).where(Ticket.status.in_(keys_of(db, {"resolved"})),
                                            Ticket.resolved_at < now() - timedelta(hours=hours))).all()
    for ticket in stale:
        change_status(db, ticket, target, SYSTEM, force=True, notify_user=False, comment="closed automatically")
    return len(stale)


# ---------------------------------------------------------------- visibility
def departments_of(db, staff_id: int) -> list[int]:
    return list(db.scalars(select(StaffDepartment.department_id).where(StaffDepartment.staff_id == staff_id)))


def visible(db, staff: Staff):
    """SQL condition for the tickets a staff member may see: their own, their
    departments' and unassigned tickets without a department; everything with tickets.view_all."""
    permissions = staff.permissions
    if "tickets.view_all" in permissions:
        return true()
    if "tickets.view" not in permissions:
        return false()
    departments = departments_of(db, staff.id)
    own = Ticket.assignee_id == staff.id
    loose = and_(Ticket.department_id.is_(None), Ticket.assignee_id.is_(None))
    if departments:
        return or_(own, Ticket.department_id.in_(departments), loose)
    return or_(own, Ticket.assignee_id.is_(None))


def can_see(db, staff: Staff, ticket: Ticket) -> bool:
    return bool(db.scalar(select(func.count()).select_from(Ticket).where(Ticket.id == ticket.id, visible(db, staff))))
