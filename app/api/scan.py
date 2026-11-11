from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_session
from app.models import Job
from app.scan import scan_library
from app.schemas import ScanStatus

router = APIRouter(prefix="/scan", tags=["scan"])

_lock = threading.Lock()
_running = False
_last_result: dict | None = None
_last_error: str | None = None


def _run_scan() -> None:
    global _running, _last_result, _last_error
    session = SessionLocal()
    job = Job(type="scan", status="running", started_at=datetime.now(timezone.utc))
    session.add(job)
    session.commit()
    try:
        result = scan_library(session)
        session.commit()
        payload = {
            "added": result.added,
            "updated": result.updated,
            "moved": result.moved,
            "missing": result.missing,
            "restored": result.restored,
            "duplicates": result.duplicates,
            "scanned_files": result.scanned_files,
            "errors": result.errors,
        }
        job.status = "done"
        job.result_json = json.dumps(payload, ensure_ascii=False)
        job.finished_at = datetime.now(timezone.utc)
        session.commit()
        _last_result = payload
        _last_error = None
    except Exception as exc:
        session.rollback()
        job.status = "failed"
        job.last_error = str(exc)
        job.finished_at = datetime.now(timezone.utc)
        session.add(job)
        session.commit()
        _last_error = str(exc)
    finally:
        session.close()
        with _lock:
            _running = False


@router.post("", status_code=202)
def trigger_scan(session: Session = Depends(get_session)) -> dict:
    global _running
    with _lock:
        if _running:
            raise HTTPException(status_code=409, detail="扫描正在进行")
        _running = True
    threading.Thread(target=_run_scan, daemon=True).start()
    return {"ok": True, "message": "已开始扫描"}


@router.get("/status", response_model=ScanStatus)
def scan_status() -> ScanStatus:
    return ScanStatus(running=_running, last_result=_last_result, last_error=_last_error)


def run_scan_inline(session: Session) -> dict:
    """供测试同步调用。"""
    result = scan_library(session)
    return {
        "added": result.added,
        "updated": result.updated,
        "moved": result.moved,
        "missing": result.missing,
        "restored": result.restored,
        "duplicates": result.duplicates,
        "scanned_files": result.scanned_files,
        "errors": result.errors,
    }
