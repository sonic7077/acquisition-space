"""Independent scheduled publish worker handling due targets, timeouts, and offline nodes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time

from sqlalchemy import select

from .db import ExecutorNode, PublishAttempt, PublishTarget, Record, now
from .operations.service import VcatOperationsService


def run_once(app) -> bool:
    Session = app.state.Session
    (app.state.data_dir / "publish-worker-heartbeat").write_text(now())
    service = getattr(app.state, "ops_service", None) or VcatOperationsService(app)
    worked = False

    with Session() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        settings = db.get(Record, "system-settings")
        ex = (settings.payload.get("executor", {}) if settings else {})
        catchup_minutes = int(ex.get("catchup_minutes", 30))
        current_utc = now()
        catchup_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=catchup_minutes)).isoformat()

        # 1. Recover expired SUBMITTING leases -> UNKNOWN (never auto-retry)
        for att in db.scalars(select(PublishAttempt).where(PublishAttempt.state == "SUBMITTING")):
            if att.lease_until and att.lease_until < current_utc:
                att.state = "UNKNOWN"
                att.error_class = "LEASE_TIMEOUT"
                att.error_message = "发布执行租约超时，结果未知，请先核对任务列表"
                att.updated_at = current_utc
                worked = True

        # 2. Pick queued server-scheduled attempts that are due
        due_ids: list[str] = []
        for att in db.scalars(select(PublishAttempt).where(PublishAttempt.state == "QUEUED").order_by(PublishAttempt.created_at)):
            target = db.get(PublishTarget, att.target_id)
            if not target or target.schedule_owner != "server":
                continue
            if target.planned_at_utc > current_utc:
                continue
            if target.planned_at_utc < catchup_cutoff:
                att.state = "MISSED"
                att.error_class = "CATCHUP_EXPIRED"
                att.error_message = f"超过 {catchup_minutes} 分钟追发窗口，请重新安排排期"
                att.updated_at = current_utc
                target.status = "MISSED"
                worked = True
                continue
            due_ids.append(att.id)
        db.commit()

    for att_id in due_ids:
        service.execute_attempt(att_id, actor=None)
        worked = True

    return worked


def main() -> None:
    from .main import create_app

    app = create_app()
    while True:
        worked = run_once(app)
        time.sleep(2 if worked else 5)


if __name__ == "__main__":
    main()
