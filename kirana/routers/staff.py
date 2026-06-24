import logging
from fastapi import APIRouter, Depends, HTTPException, Request

from kirana.service import KiranaService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/kirana", tags=["Kirana AI"])


def _svc(request: Request) -> KiranaService:
    return request.app.state.kirana_service


def _auth(request: Request):
    s = request.app.state.settings
    api_key = request.headers.get("X-API-Key", "")
    auth_hdr = request.headers.get("Authorization", "")
    bearer = auth_hdr[len("Bearer ") :] if auth_hdr.startswith("Bearer ") else ""
    if api_key and api_key == s.kirana_api_key:
        return {"role": "admin", "user_id": None, "store_id": None}
    if bearer:
        user = _svc(request).user_by_token(bearer)
        if user:
            return user
    raise HTTPException(status_code=401, detail="Unauthorized")


def _repo(request: Request):
    from kirana.repositories.main import KiranaRepository
    return KiranaRepository(request.app.state.engine)


def _sid(user: dict) -> int:
    if not user.get("store_id"):
        raise HTTPException(status_code=403, detail="Store owner login required")
    return int(user["store_id"])


@router.get("/staff")
async def list_staff(request: Request, user: dict = Depends(_auth)):
    return {"staff": _repo(request).list_staff(_sid(user))}


@router.post("/staff")
async def create_staff(request: Request, user: dict = Depends(_auth)):
    b = await request.json()
    if not b.get("name"):
        raise HTTPException(status_code=400, detail="name required")
    return _repo(request).create_staff(
        _sid(user), name=b["name"], phone=b.get("phone"), role=b.get("role"),
        commission_pct=b.get("commission_pct") or 0, user_id=b.get("user_id"))


@router.patch("/staff/{staff_id}")
async def update_staff(staff_id: int, request: Request, user: dict = Depends(_auth)):
    b = await request.json()
    res = _repo(request).update_staff(staff_id, _sid(user),
        name=b.get("name"), phone=b.get("phone"), role=b.get("role"),
        commission_pct=b.get("commission_pct"), is_active=b.get("is_active"))
    if not res:
        raise HTTPException(status_code=404, detail="Staff not found")
    return res


@router.get("/staff/attendance")
async def list_attendance(request: Request, date: str, user: dict = Depends(_auth)):
    return {"attendance": _repo(request).list_attendance(_sid(user), date)}


@router.get("/staff/{staff_id}/attendance/history")
async def attendance_history(staff_id: int, request: Request, days: int = 30,
                              user: dict = Depends(_auth)):
    return _repo(request).attendance_history(_sid(user), staff_id, days)


@router.post("/staff/attendance")
async def mark_attendance(request: Request, user: dict = Depends(_auth)):
    b = await request.json()
    if not b.get("staff_id") or not b.get("date"):
        raise HTTPException(status_code=400, detail="staff_id and date required")
    return _repo(request).mark_attendance(
        _sid(user), int(b["staff_id"]), b["date"], b.get("status") or "present")


@router.get("/staff/tasks")
async def list_tasks(request: Request, user: dict = Depends(_auth)):
    inc = request.query_params.get("include_done") != "false"
    return {"tasks": _repo(request).list_tasks(_sid(user), include_done=inc)}


@router.post("/staff/tasks")
async def create_task(request: Request, user: dict = Depends(_auth)):
    b = await request.json()
    if not b.get("title"):
        raise HTTPException(status_code=400, detail="title required")
    return _repo(request).create_task(
        _sid(user), b["title"], staff_id=b.get("staff_id"), due_date=b.get("due_date"))


@router.patch("/staff/tasks/{task_id}")
async def set_task(task_id: int, request: Request, user: dict = Depends(_auth)):
    b = await request.json()
    if not _repo(request).set_task_done(task_id, _sid(user), bool(b.get("is_done", True))):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"updated": True}


@router.get("/staff/performance")
async def staff_performance(request: Request, days: int = 30, user: dict = Depends(_auth)):
    return _repo(request).staff_performance(_sid(user), days)


# ── Admin: per-store staff view + bulk add ────────────────────────────────────


@router.get("/admin/stores/{store_id}/staff")
async def admin_list_staff(store_id: int, request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return {"staff": _repo(request).list_staff(store_id)}


@router.post("/admin/stores/{store_id}/staff/bulk")
async def admin_bulk_staff(store_id: int, request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    b = await request.json()
    rows = b.get("staff") or []
    created = []
    for r in rows:
        if not r.get("name"):
            continue
        created.append(_repo(request).create_staff(
            store_id, name=r["name"], phone=r.get("phone"), role=r.get("role"),
            commission_pct=r.get("commission_pct") or 0, user_id=r.get("user_id")))
    return {"created": len(created), "staff": created}
