#!/usr/bin/env python3
"""Seed a bit of demo state so the live dashboard has something to show:
a few overdue task runs, expiring/expired evidence, and policies due for review.

Run after init_db + build_control_library.
Usage: .venv/bin/python scripts/seed_demo.py
"""

import datetime as dt
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import text  # noqa: E402

from _db import get_engine  # noqa: E402

TODAY = dt.date.today()


def iso(days: int) -> str:
    return (TODAY + dt.timedelta(days=days)).isoformat()


def uid() -> str:
    return str(uuid.uuid4())


def main() -> None:
    engine = get_engine()
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with engine.begin() as conn:
        tenant_id = conn.execute(text("SELECT id FROM tenants LIMIT 1")).scalar()
        member = conn.execute(text(
            "SELECT id FROM tenant_members WHERE tenant_id = :t LIMIT 1"),
            {"t": tenant_id}).scalar()

        # demo tasks with backdated runs so the dashboard shows overdue items.
        # Runs are inserted 'pending' on purpose — the engine's mark_overdue()
        # owns the transition and raises the notification.
        for title, days, cadence in [("[demo] Quarterly access recertification", -6, 3),
                                     ("[demo] Annual ISP review", -12, 12),
                                     ("[demo] Monthly firewall rule review", 1, 1)]:
            task_id = uid()
            conn.execute(text(
                "INSERT INTO tasks (id,tenant_id,title,cadence_months,assignee_member_id,"
                "next_due_at,status,created_at) VALUES (:i,:t,:ti,:c,:m,:d,:s,:n)"),
                {"i": task_id, "t": tenant_id, "ti": title, "c": cadence,
                 "m": member, "d": iso(days), "s": "active", "n": now})
            conn.execute(text(
                "INSERT INTO task_runs (id,task_id,due_at,status) VALUES (:i,:tk,:d,:s)"),
                {"i": uid(), "tk": task_id, "d": iso(days), "s": "pending"})

        # Evidence with validity windows, seeded as LINK artifacts.
        # The v2 schema rightly forbids "FULFILLED but empty" (ev_fulfilled_has_body):
        # a fulfilled artifact must actually exist. Demo rows therefore point at a URL
        # rather than pretending to be a file with no bytes behind it.
        for title, etype, valid_days in [("ISO 27001 Certificate", "certificate", 9),
                                         ("VAPT Report Q1 2026", "report", 52),
                                         ("Firewall Rule Base review", "report", -10),
                                         ("Cyber Insurance Policy", "insurance", 260)]:
            slug = title.lower().replace(" ", "-")
            conn.execute(text(
                "INSERT INTO evidence (id,tenant_id,title,evidence_type,medium,state,"
                "external_url,issued_at,valid_until,created_by_member_id,created_at) "
                "VALUES (:i,:t,:ti,:e,'LINK','FULFILLED',:u,:is,:v,:m,:c)"),
                {"i": uid(), "t": tenant_id, "ti": f"[demo] {title}", "e": etype,
                 "u": f"https://intranet.kiam.example/compliance/{slug}",
                 "is": iso(-300), "v": iso(valid_days), "m": member, "c": now})

        # policies due for review
        for title, cadence, due_days in [("Information Security Policy", 12, -11),
                                         ("Incident Response Policy", 12, 18),
                                         ("Access Control Policy", 12, 130)]:
            conn.execute(text(
                "INSERT INTO policies (id,tenant_id,title,owner_member_id,"
                "review_cadence_months,next_review_at,status,created_at) "
                "VALUES (:i,:t,:ti,:m,:c,:d,:s,:n)"),
                {"i": uid(), "t": tenant_id, "ti": f"[demo] {title}", "m": member,
                 "c": cadence, "d": iso(due_days), "s": "active", "n": now})

    print("Seeded demo tasks, evidence, and policies for the dashboard.")


if __name__ == "__main__":
    main()
