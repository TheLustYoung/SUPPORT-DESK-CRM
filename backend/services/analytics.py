"""Reports and the dashboard overview. Every metric follows a documented rule
(docs/ANALYTICS.md); the tests check the rules on known data.

Common filters: period (from–to, the ticket's creation time), language, department, agent (assignee), priority,
status. Times are in seconds; averages and medians count only tickets that have the measured moment.
"""
import csv
import io
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from sqlalchemy import and_, func, select, true
from backend.models import (Campaign, CampaignDelivery, Department, ErrorLog, FaqVersion, Message, Staff, Ticket, TgUser,
                            UserEvent, now)
from backend.services import faq, tickets
from backend.services.i18n import pick

FILTERS = ("date_from", "date_to", "language", "department_id", "assignee_id", "priority", "status")


def period(filters: dict) -> tuple[datetime, datetime]:
    """[start, end) in UTC; default: the last 30 days."""
    today = now().date()
    start = _date(filters.get("date_from")) or today - timedelta(days=29)
    end = _date(filters.get("date_to")) or today
    return (datetime.combine(start, time.min, tzinfo=timezone.utc), datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc))


def _date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _ticket_query(db, filters: dict, staff=None):
    start, end = period(filters)
    stmt = select(Ticket).where(Ticket.created_at >= start, Ticket.created_at < end)
    for key, column in (("language", Ticket.language), ("priority", Ticket.priority), ("status", Ticket.status)):
        if filters.get(key):
            stmt = stmt.where(column == filters[key])
    for key, column in (("department_id", Ticket.department_id), ("assignee_id", Ticket.assignee_id)):
        if filters.get(key) not in (None, ""):
            stmt = stmt.where(column == int(filters[key]))
    if staff is not None:
        stmt = stmt.where(tickets.visible(db, staff))
    return stmt


def _speakers(filters: dict):
    """Users of the language filter: their chosen bot language, else their Telegram language (None = no filter)."""
    code = (filters.get("language") or "").strip().lower()
    if not code:
        return None
    spoken = func.lower(func.coalesce(TgUser.language, TgUser.tg_language, ""))
    return select(TgUser.id).where((spoken == code) | spoken.like(f"{code}-%"))


def _stats(values: list[float]) -> dict:
    return {"average": round(statistics.fmean(values)) if values else None,
            "median": round(statistics.median(values)) if values else None, "count": len(values)}


def _seconds(start, end) -> float | None:
    return (end - start).total_seconds() if start and end else None


def ticket_report(db, filters: dict) -> dict:
    rows = db.scalars(_ticket_query(db, filters)).all()
    stages = tickets.statuses(db)
    stage = lambda t: stages[t.status].stage if t.status in stages else "open"
    responses = [s for t in rows if (s := _seconds(t.created_at, t.first_response_at)) is not None]
    resolutions = [s for t in rows if (s := _seconds(t.created_at, t.resolved_at)) is not None]
    with_response_target = [t for t in rows if t.due_response_at and (t.first_response_at or t.response_breached)]
    with_resolution_target = [t for t in rows if t.due_resolution_at and (t.resolved_at or t.resolution_breached)]
    met = sum(1 for t in with_response_target if not t.response_breached) + sum(1 for t in with_resolution_target if not t.resolution_breached)
    measured = len(with_response_target) + len(with_resolution_target)
    by_day = Counter(t.created_at.date().isoformat() for t in rows)
    resolved_by_day = Counter(t.resolved_at.date().isoformat() for t in rows if t.resolved_at)
    start, end = period(filters)
    days = [(start.date() + timedelta(days=i)).isoformat() for i in range((end.date() - start.date()).days)]
    ratings = [t.rating for t in rows if t.rating]
    return {
        "total": len(rows),
        "by_status": dict(Counter(t.status for t in rows)), "by_priority": dict(Counter(t.priority for t in rows)),
        "by_category": dict(Counter(str(t.category_id or "") for t in rows)), "by_department": dict(Counter(str(t.department_id or "") for t in rows)),
        "open": sum(1 for t in rows if stage(t) not in tickets.FINAL),
        "overdue": sum(1 for t in rows if stage(t) not in tickets.FINAL and (t.response_breached or t.resolution_breached)),
        "resolved": sum(1 for t in rows if t.resolved_at), "reopened": sum(1 for t in rows if t.reopen_count),
        "first_response": _stats(responses), "resolution": _stats(resolutions),
        "sla_compliance": round(100 * met / measured, 1) if measured else None,
        "csat": round(statistics.fmean(ratings), 2) if ratings else None, "ratings": len(ratings),
        "trend": [{"day": d, "created": by_day.get(d, 0), "resolved": resolved_by_day.get(d, 0)} for d in days],
    }


def user_report(db, filters: dict) -> dict:
    start, end = period(filters)
    speakers = _speakers(filters)
    within = (lambda column: column.in_(speakers)) if speakers is not None else (lambda column: true())
    active_ids = set(db.scalars(select(UserEvent.user_id).where(UserEvent.created_at >= start, UserEvent.created_at < end,
                                                                 within(UserEvent.user_id)).distinct()))
    active_ids |= set(db.scalars(select(Message.user_id).where(Message.direction == "in", Message.created_at >= start, Message.created_at < end,
                                                               within(Message.user_id)).distinct()))
    new_ids = set(db.scalars(select(TgUser.id).where(TgUser.first_seen >= start, TgUser.first_seen < end, within(TgUser.id))))
    by_language = Counter(db.scalars(select(func.coalesce(TgUser.language, TgUser.tg_language, "")).where(TgUser.id.in_(active_ids)))) if active_ids else Counter()
    ticket_rows = db.scalars(_ticket_query(db, filters)).all()
    per_user = Counter(t.user_id for t in ticket_rows)
    return {"total": db.scalar(select(func.count()).select_from(TgUser).where(within(TgUser.id))), "new": len(new_ids), "active": len(active_ids),
            "returning": len(active_ids - new_ids), "by_language": dict(by_language),
            "tickets_per_user": round(statistics.fmean(per_user.values()), 2) if per_user else 0,
            "top_categories": Counter(str(t.category_id or "") for t in ticket_rows).most_common(10)}


def faq_report(db, filters: dict, lang: str = "en") -> dict:
    start, end = period(filters)
    published = faq.latest(db)
    tree = faq.Tree(published.snapshot if published else None)
    title = lambda node_id: pick((tree.get(node_id) or {}).get("title"), lang) or f"#{node_id}"
    speakers = _speakers(filters)
    of_users = [UserEvent.user_id.in_(speakers)] if speakers is not None else []
    opens = db.execute(select(UserEvent.user_id, UserEvent.ref_id, UserEvent.data).where(
        UserEvent.kind == "faq_open", UserEvent.created_at >= start, UserEvent.created_at < end, *of_users)).all()
    visits = Counter(ref for _, ref, _ in opens if ref)
    categories = Counter()
    paths = Counter()
    for _, ref, data in opens:
        path = (data or {}).get("path") or tree.path(ref)
        if path:
            categories[path[0]] += 1
            if not tree.children(ref):
                paths[" › ".join(title(n) for n in path)] += 1
    from_faq = Counter(t.faq_node_id for t in db.scalars(_ticket_query(db, filters)) if t.faq_node_id)
    misses = Counter((d or {}).get("query", "").strip().lower()[:100] for d in db.scalars(select(UserEvent.data).where(
        UserEvent.kind == "search_miss", UserEvent.created_at >= start, UserEvent.created_at < end, *of_users)))
    return {"top_questions": [{"id": n, "title": title(n), "count": c} for n, c in visits.most_common(15)],
            "top_categories": [{"id": n, "title": title(n), "count": c} for n, c in categories.most_common(10)],
            "paths": [{"path": p, "count": c} for p, c in paths.most_common(15)],
            "lead_to_tickets": [{"id": n, "title": title(n), "count": c} for n, c in from_faq.most_common(10)],
            "missing": [{"query": q, "count": c} for q, c in misses.most_common(20) if q]}


def agent_report(db, filters: dict) -> dict:
    rows = db.scalars(_ticket_query(db, filters)).all()
    names = {s.id: s.name for s in db.scalars(select(Staff))}
    departments = {d.id: pick(d.name, "en") for d in db.scalars(select(Department))}
    grouped, teams = defaultdict(list), defaultdict(list)
    for t in rows:
        grouped[t.assignee_id].append(t)
        teams[t.department_id].append(t)

    def block(items):
        responses = [s for t in items if (s := _seconds(t.created_at, t.first_response_at)) is not None]
        resolutions = [s for t in items if (s := _seconds(t.created_at, t.resolved_at)) is not None]
        measured = [t for t in items if t.due_response_at and (t.first_response_at or t.response_breached)]
        return {"tickets": len(items), "resolved": sum(1 for t in items if t.resolved_at), "first_response": _stats(responses),
                "resolution": _stats(resolutions),
                "sla_compliance": round(100 * sum(1 for t in measured if not t.response_breached) / len(measured), 1) if measured else None}

    open_load = tickets.open_load(db, [k for k in grouped if k])
    replies = Counter(db.scalars(select(Message.staff_id).where(Message.direction == "out", Message.staff_id.is_not(None),
                                                                Message.created_at >= period(filters)[0], Message.created_at < period(filters)[1])))
    return {"agents": [{"id": k, "name": names.get(k, "—") if k else "unassigned", "open_now": open_load.get(k, 0),
                        "replies": replies.get(k, 0), **block(v)} for k, v in grouped.items()],
            "departments": [{"id": k, "name": departments.get(k, "—") if k else "none", **block(v)} for k, v in teams.items()]}


def campaign_report(db, filters: dict) -> dict:
    start, end = period(filters)
    items = db.scalars(select(Campaign).where(Campaign.created_at >= start, Campaign.created_at < end).order_by(Campaign.id.desc())).all()
    language = (filters.get("language") or "").strip()
    speakers = _speakers(filters)
    in_language = [CampaignDelivery.language == language] if language else []  # the language each recipient got
    result = []
    for campaign in items:
        counts = dict(db.execute(select(CampaignDelivery.status, func.count()).where(CampaignDelivery.campaign_id == campaign.id, *in_language)
                                 .group_by(CampaignDelivery.status)).all())
        recipients = sum(counts.values()) if language else campaign.recipients
        result.append({"id": campaign.id, "name": campaign.name, "status": campaign.status, "recipients": recipients, **counts})
    failures = Counter(db.scalars(select(Message.error).where(Message.delivery.in_(("failed", "blocked")), Message.created_at >= start,
                                                              Message.created_at < end, *([Message.user_id.in_(speakers)] if speakers is not None else []))))
    failures.update(db.scalars(select(CampaignDelivery.error).join(Campaign, Campaign.id == CampaignDelivery.campaign_id).where(
        CampaignDelivery.status.in_(("failed", "blocked")), Campaign.created_at >= start, Campaign.created_at < end, *in_language)))
    return {"campaigns": result, "errors": [{"error": e or "unknown", "count": c} for e, c in failures.most_common(20)]}


REPORTS = {"tickets": ticket_report, "users": user_report, "faq": faq_report, "agents": agent_report, "campaigns": campaign_report}


def overview(db, staff) -> dict:
    """The dashboard's first page, limited to the tickets the staff member may see."""
    moment = now()
    visible = tickets.visible(db, staff)
    stages = tickets.statuses(db)
    by_stage = Counter()
    for status, number in db.execute(select(Ticket.status, func.count()).where(visible).group_by(Ticket.status)):
        by_stage[stages[status].stage if status in stages else "open"] += number
    final = tickets.keys_of(db, tickets.FINAL)
    overdue = db.scalar(select(func.count()).select_from(Ticket).where(visible, Ticket.status.notin_(final),
                                                                        (Ticket.response_breached | Ticket.resolution_breached)))
    week = moment - timedelta(days=7)
    report = ticket_report(db, {"date_from": week.date().isoformat(), "date_to": moment.date().isoformat()})
    day = moment - timedelta(days=1)
    return {
        "users": {"total": db.scalar(select(func.count()).select_from(TgUser)),
                  "active_7d": db.scalar(select(func.count()).select_from(TgUser).where(TgUser.last_seen >= week))},
        "tickets": {stage: by_stage.get(stage, 0) for stage in tickets.STAGES}, "overdue": overdue,
        "messages_24h": {"in": db.scalar(select(func.count()).select_from(Message).where(Message.direction == "in", Message.created_at >= day)),
                         "out": db.scalar(select(func.count()).select_from(Message).where(Message.direction.in_(("out", "system")), Message.created_at >= day))},
        "first_response_7d": report["first_response"], "resolution_7d": report["resolution"], "sla_7d": report["sla_compliance"],
        "csat_7d": report["csat"], "agents": agent_report(db, {"date_from": week.date().isoformat()})["agents"],
        "faq": faq_report(db, {"date_from": week.date().isoformat()})["top_categories"][:5],
        "delivery_failures_24h": db.scalar(select(func.count()).select_from(Message).where(Message.delivery.in_(("failed", "blocked")), Message.created_at >= day)),
        "errors_24h": db.scalar(select(func.count()).select_from(ErrorLog).where(ErrorLog.created_at >= day)),
        "queue": db.scalar(select(func.count()).select_from(Message).where(Message.delivery.in_(("queued", "sending")))),
        "faq_published": bool(db.scalar(select(func.count()).select_from(FaqVersion))),
        "trend": report["trend"],
    }


# ---------------------------------------------------------------- export (CSV, Excel, PDF)
def named(db, report: dict, lang: str) -> dict:
    """An export shows categories and departments by name (the screen maps the ids itself)."""
    from backend.models import Category
    names = {"by_category": {str(c.id): pick(c.name, lang) for c in db.scalars(select(Category))},
             "by_department": {str(d.id): pick(d.name, lang) for d in db.scalars(select(Department))}}
    names["top_categories"] = names["by_category"]
    result = dict(report)
    for key, table in names.items():
        value = report.get(key)
        if isinstance(value, dict):
            result[key] = {table.get(k, k) or "—": v for k, v in value.items()}
        elif isinstance(value, list) and value and isinstance(value[0], (list, tuple)):
            result[key] = [[table.get(str(k), k) or "—", v] for k, v in value]
    return result


def flatten(report: dict) -> list[tuple[str, list[str], list[list]]]:
    """A report as tables: (title, header, rows)."""
    tables, summary = [], []
    for key, value in report.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            header = list(dict.fromkeys(k for item in value for k in item))
            tables.append((key, header, [[_cell(item.get(h)) for h in header] for item in value]))
        elif isinstance(value, list) and value and isinstance(value[0], (list, tuple)):
            tables.append((key, ["value", "count"], [[_cell(x) for x in v] for v in value]))
        elif isinstance(value, dict):
            if value and all(not isinstance(v, (dict, list)) for v in value.values()):
                tables.append((key, ["key", "value"], [[_cell(k), _cell(v)] for k, v in value.items()]))
            else:
                summary += [[_cell(f"{key}.{k}"), _cell(v)] for k, v in value.items()]
        else:
            summary.append([key, _cell(value)])
    return [("summary", ["metric", "value"], summary), *tables] if summary else tables


FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value):
    if isinstance(value, dict):
        value = ", ".join(f"{k}: {v}" for k, v in value.items())
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str) and value.startswith(FORMULA_START):
        # Texts from users (search queries, names) must stay text in Excel and LibreOffice, never run as a formula
        return "'" + value
    return value


def to_csv(report: dict) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    for title, header, rows in flatten(report):
        writer.writerow([f"# {title}"])
        writer.writerow(header)
        writer.writerows(rows)
        writer.writerow([])
    return ("\ufeff" + out.getvalue()).encode("utf-8")  # BOM: Excel opens Arabic, Cyrillic and emoji correctly


def to_xlsx(report: dict) -> bytes:
    from openpyxl import Workbook
    book = Workbook()
    book.remove(book.active)
    for title, header, rows in flatten(report):
        sheet = book.create_sheet(title[:31])
        sheet.append(header)
        for row in rows:
            sheet.append([str(v) if isinstance(v, (list, tuple)) else v for v in row])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def to_pdf(report: dict, name: str) -> bytes:
    """A simple tabular PDF. Uses a Unicode font when one is installed (DejaVu in the Docker image)."""
    from fpdf import FPDF
    pdf = FPDF(orientation="L")
    font = "Helvetica"
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf"):
        try:
            pdf.add_font("Uni", "", path)
            font = "Uni"
            break
        except (OSError, RuntimeError):
            continue
    pdf.add_page()
    pdf.set_font(font, size=14)
    pdf.cell(0, 10, f"{name} report", new_x="LMARGIN", new_y="NEXT")
    for title, header, rows in flatten(report):
        pdf.set_font(font, size=11)
        pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font(font, size=8)
        width = (pdf.w - 20) / max(len(header), 1)
        for row in [header, *rows][:200]:
            for value in row:
                text = str("" if value is None else value)
                if font != "Uni":
                    text = text.encode("latin-1", "replace").decode("latin-1")
                pdf.cell(width, 6, text[:40], border=1)
            pdf.ln()
        pdf.ln(3)
    return bytes(pdf.output())
