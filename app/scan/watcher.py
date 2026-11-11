from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.config import get_settings
from app.db import SessionLocal
from app.scan.scanner import scan_library

logger = logging.getLogger(__name__)

_stop = threading.Event()
_threads: list[threading.Thread] = []
_scan_lock = threading.Lock()


def trigger_watch_scan(reason: str = "watch") -> None:
    if not _scan_lock.acquire(blocking=False):
        logger.debug("扫描已在进行，忽略（%s）", reason)
        return
    try:
        settings = get_settings()
        session = SessionLocal()
        try:
            result = scan_library(session, settings.library_path)
            session.commit()
            logger.info(
                "监听扫描（%s）完成：added=%s updated=%s moved=%s missing=%s",
                reason,
                result.added,
                result.updated,
                result.moved,
                result.missing,
            )
        except Exception:
            session.rollback()
            logger.exception("监听扫描失败（%s）", reason)
        finally:
            session.close()
    finally:
        _scan_lock.release()


def start_watcher() -> None:
    """启动 inotify（或 Watchdog 原生观察者）以及定时轮询兜底。"""
    stop_watcher()
    settings = get_settings()
    if not settings.watch_enabled:
        logger.info("文件监听已关闭（WATCH_ENABLED=false）")
        return
    root = settings.library_path
    root.mkdir(parents=True, exist_ok=True)
    _stop.clear()

    debounce = threading.Event()
    pending_reason = {"value": "event"}

    def debounced_worker():
        while not _stop.wait(settings.watch_debounce_seconds):
            if debounce.is_set():
                debounce.clear()
                trigger_watch_scan(pending_reason["value"])

    t = threading.Thread(target=debounced_worker, name="opds-watch-debounce", daemon=True)
    t.start()
    _threads.append(t)

    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):  # noqa: ANN001
                name = Path(getattr(event, "src_path", "") or "").name
                if name.startswith(".") or name == ".opds-library-write-test":
                    return
                pending_reason["value"] = f"fs:{getattr(event, 'event_type', 'change')}"
                debounce.set()

        observer = Observer()
        observer.schedule(Handler(), str(root.resolve()), recursive=True)
        observer.start()

        def observer_watch():
            try:
                while not _stop.wait(1):
                    if not observer.is_alive():
                        logger.warning("inotify 观察者已停止，后续依赖定时轮询")
                        break
            finally:
                try:
                    observer.stop()
                    observer.join(timeout=3)
                except Exception:
                    pass

        t2 = threading.Thread(target=observer_watch, name="opds-watch-inotify", daemon=True)
        t2.start()
        _threads.append(t2)
        logger.info("已启动书库文件监听（inotify/watchdog）：%s", root)
    except Exception:
        logger.exception("无法启动 inotify/watchdog，将仅使用定时轮询（常见于部分 NAS 挂载）")

    interval = max(int(settings.watch_poll_seconds), 15)

    def poll_worker():
        # 启动后稍等，避免与首次手动扫描重叠
        if _stop.wait(min(interval, 30)):
            return
        while not _stop.is_set():
            pending_reason["value"] = "poll"
            trigger_watch_scan("poll")
            if _stop.wait(interval):
                break

    t3 = threading.Thread(target=poll_worker, name="opds-watch-poll", daemon=True)
    t3.start()
    _threads.append(t3)
    logger.info("已启动定时轮询兜底，间隔 %s 秒", interval)


def stop_watcher() -> None:
    _stop.set()
    for t in list(_threads):
        t.join(timeout=2)
    _threads.clear()
    _stop.clear()
