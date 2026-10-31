"""Reports: filtered by period, language, department, agent, priority and status; exported to CSV, Excel
or PDF. Exports are recorded in the audit log."""
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from backend.api.deps import staff_required
from backend.database import session
from backend.services import analytics, audit

router = APIRouter(prefix="/api/admin", tags=["analytics"])
TYPES = {"csv": ("text/csv; charset=utf-8", analytics.to_csv), "xlsx": (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", analytics.to_xlsx)}


def _filters(date_from, date_to, language, department_id, assignee_id, priority, status) -> dict:
    return {"date_from": date_from, "date_to": date_to, "language": language, "department_id": department_id,
            "assignee_id": assignee_id, "priority": priority, "status": status}


@router.get("/analytics/{name}")
def report(name: str, date_from: str = "", date_to: str = "", language: str = "", department_id: str = "", assignee_id: str = "",
           priority: str = "", status: str = "", lang: str = "en", staff=Depends(staff_required), db=Depends(session)):
    filters = _filters(date_from, date_to, language, department_id, assignee_id, priority, status)
    return analytics.faq_report(db, filters, lang) if name == "faq" else analytics.REPORTS[name](db, filters)


@router.get("/analytics/{name}/export")
def export(name: str, request: Request, format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"), date_from: str = "", date_to: str = "",
           language: str = "", department_id: str = "", assignee_id: str = "", priority: str = "", status: str = "",
           lang: str = "en", staff=Depends(staff_required), db=Depends(session)):
    filters = _filters(date_from, date_to, language, department_id, assignee_id, priority, status)
    report = analytics.faq_report(db, filters, lang) if name == "faq" else analytics.REPORTS[name](db, filters)
    data = analytics.named(db, report, lang)  # the same numbers as on screen, with names instead of ids
    if format == "pdf":
        body, media_type = analytics.to_pdf(data, name.capitalize()), "application/pdf"
    else:
        media_type, convert = TYPES[format]
        body = convert(data)
    audit.record(db, staff, "analytics.export", "report", name, {"format": format, **{k: v for k, v in filters.items() if v}}, request.state.ip)
    db.commit()
    return Response(body, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{name}-report.{format}"'})
