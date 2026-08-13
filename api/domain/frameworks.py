"""Certification frameworks and their clauses (P5-S9 Slice B).

**The distinction this module exists to hold:**

    a framework CLAUSE is what an auditor ASKS   — "ISO A.8.5", "SOC 2 CC6.1"
    a CONTROL          is what we DO             — "MFA enforced on privileged accounts"

`control_clause_map` joins them **many-to-many**, which is the whole design. One control
satisfies ISO 27001 *and* SOC 2 *and* an RBI clause simultaneously, so the evidence is
gathered once and counts toward every certification. Organising master controls *per
certification* — a SOC 2 set, a HIPAA set, an ISO set — would duplicate "MFA on admin
accounts" three times with three owners and three evidence trails. `db/schema.sql` calls that
out by name on `control_clause_map`: *"the explicit fix for Probo's single-framework
Control."*

These three tables (`frameworks`, `framework_clauses`, `control_clause_map`) have existed
since M13 with **zero rows, no API and no writer**. This module is the first thing to populate
them.

──────────────────────────────────────────────────────────────────────────────────────────
**Why there is no standard text in this file, and why that is deliberate.**

ISO/IEC 27001's Annex A text and the AICPA's Trust Services Criteria are **copyrighted**.
Reproducing their clause descriptions inside the product would be republishing a licensed
standard. What is *not* restricted is the bare reference and its short title — "A.8.5 Secure
authentication" — which is a factual identifier, the same way a legal citation is.

So every seeded clause carries `ref` + `title` and leaves `description` NULL. A customer who
holds a licence can paste the official wording into `description` themselves, and the
spreadsheet import exists precisely so an organisation can bring its own clause list. If a
licensed text is ever bundled, it belongs behind a per-tenant import, not in this file.
"""

from __future__ import annotations

import uuid

from sqlalchemy import insert, select

from api.core.database import t
from api.core.util import now_iso

#: code -> (name, version, [(ref, title), ...])
#: Refs and short titles ONLY — see the licensing note above.
BASELINE: dict[str, tuple[str, str | None, list[tuple[str, str]]]] = {
    "ISO27001-2022": ("ISO/IEC 27001:2022 Annex A", "2022", [
        # A.5 Organisational
        ("A.5.1", "Policies for information security"),
        ("A.5.2", "Information security roles and responsibilities"),
        ("A.5.3", "Segregation of duties"),
        ("A.5.7", "Threat intelligence"),
        ("A.5.8", "Information security in project management"),
        ("A.5.9", "Inventory of information and other associated assets"),
        ("A.5.10", "Acceptable use of information and other associated assets"),
        ("A.5.12", "Classification of information"),
        ("A.5.15", "Access control"),
        ("A.5.16", "Identity management"),
        ("A.5.17", "Authentication information"),
        ("A.5.18", "Access rights"),
        ("A.5.19", "Information security in supplier relationships"),
        ("A.5.20", "Addressing information security within supplier agreements"),
        ("A.5.21", "Managing information security in the ICT supply chain"),
        ("A.5.22", "Monitoring, review and change management of supplier services"),
        ("A.5.23", "Information security for use of cloud services"),
        ("A.5.24", "Information security incident management planning and preparation"),
        ("A.5.25", "Assessment and decision on information security events"),
        ("A.5.26", "Response to information security incidents"),
        ("A.5.29", "Information security during disruption"),
        ("A.5.30", "ICT readiness for business continuity"),
        ("A.5.31", "Legal, statutory, regulatory and contractual requirements"),
        ("A.5.34", "Privacy and protection of PII"),
        ("A.5.35", "Independent review of information security"),
        # A.6 People
        ("A.6.1", "Screening"),
        ("A.6.2", "Terms and conditions of employment"),
        ("A.6.3", "Information security awareness, education and training"),
        ("A.6.4", "Disciplinary process"),
        ("A.6.5", "Responsibilities after termination or change of employment"),
        ("A.6.6", "Confidentiality or non-disclosure agreements"),
        ("A.6.7", "Remote working"),
        ("A.6.8", "Information security event reporting"),
        # A.7 Physical
        ("A.7.1", "Physical security perimeters"),
        ("A.7.2", "Physical entry"),
        ("A.7.4", "Physical security monitoring"),
        ("A.7.10", "Storage media"),
        ("A.7.11", "Supporting utilities"),
        ("A.7.14", "Secure disposal or re-use of equipment"),
        # A.8 Technological
        ("A.8.1", "User endpoint devices"),
        ("A.8.2", "Privileged access rights"),
        ("A.8.3", "Information access restriction"),
        ("A.8.5", "Secure authentication"),
        ("A.8.6", "Capacity management"),
        ("A.8.7", "Protection against malware"),
        ("A.8.8", "Management of technical vulnerabilities"),
        ("A.8.9", "Configuration management"),
        ("A.8.10", "Information deletion"),
        ("A.8.12", "Data leakage prevention"),
        ("A.8.13", "Information backup"),
        ("A.8.15", "Logging"),
        ("A.8.16", "Monitoring activities"),
        ("A.8.20", "Networks security"),
        ("A.8.21", "Security of network services"),
        ("A.8.22", "Segregation of networks"),
        ("A.8.23", "Web filtering"),
        ("A.8.24", "Use of cryptography"),
        ("A.8.25", "Secure development life cycle"),
        ("A.8.26", "Application security requirements"),
        ("A.8.28", "Secure coding"),
        ("A.8.29", "Security testing in development and acceptance"),
        ("A.8.31", "Separation of development, test and production environments"),
        ("A.8.32", "Change management"),
    ]),
    "SOC2": ("SOC 2 Trust Services Criteria", "2017 (rev. 2022)", [
        ("CC1.1", "Commitment to integrity and ethical values"),
        ("CC1.2", "Board independence and oversight"),
        ("CC1.3", "Management establishes structures, reporting lines and authorities"),
        ("CC1.4", "Commitment to attract, develop and retain competent individuals"),
        ("CC1.5", "Holds individuals accountable for internal control responsibilities"),
        ("CC2.1", "Uses relevant, quality information"),
        ("CC2.2", "Communicates internal control information internally"),
        ("CC2.3", "Communicates with external parties"),
        ("CC3.1", "Specifies objectives to identify and assess risk"),
        ("CC3.2", "Identifies and analyses risk"),
        ("CC3.3", "Considers the potential for fraud"),
        ("CC3.4", "Identifies and assesses changes affecting internal control"),
        ("CC4.1", "Selects and performs ongoing evaluations"),
        ("CC4.2", "Evaluates and communicates deficiencies"),
        ("CC5.1", "Selects and develops control activities"),
        ("CC5.2", "Selects and develops general controls over technology"),
        ("CC5.3", "Deploys control activities through policies and procedures"),
        ("CC6.1", "Logical access security software and infrastructure"),
        ("CC6.2", "Registration and authorisation of new users"),
        ("CC6.3", "Access modification and removal"),
        ("CC6.4", "Physical access restriction"),
        ("CC6.5", "Disposal of physical and logical protections"),
        ("CC6.6", "Security measures against threats from outside the system"),
        ("CC6.7", "Restricts the transmission and movement of information"),
        ("CC6.8", "Controls to prevent or detect unauthorised software"),
        ("CC7.1", "Detects and monitors configuration changes and vulnerabilities"),
        ("CC7.2", "Monitors system components for anomalies"),
        ("CC7.3", "Evaluates security events and responds"),
        ("CC7.4", "Responds to identified security incidents"),
        ("CC7.5", "Recovers from identified security incidents"),
        ("CC8.1", "Change management over infrastructure, data and software"),
        ("CC9.1", "Identifies and mitigates business disruption risks"),
        ("CC9.2", "Assesses and manages vendor and business partner risks"),
        ("A1.1", "Availability — capacity management"),
        ("A1.2", "Availability — backup, recovery and environmental protection"),
        ("A1.3", "Availability — recovery plan testing"),
        ("C1.1", "Confidentiality — identification and maintenance"),
        ("C1.2", "Confidentiality — disposal"),
    ]),
    "RBI-ITO": ("RBI — Outsourcing of IT Services", "2023", [
        ("2.1", "Board-approved IT outsourcing policy"),
        ("2.2", "Comprehensive risk assessment before outsourcing"),
        ("3.1", "Due diligence on the service provider"),
        ("3.2", "Financial and operational capability assessment"),
        ("4.1", "Outsourcing agreement — scope, SLAs and termination"),
        ("4.2", "Right to audit and inspection"),
        ("5.1", "Monitoring and review of the service provider"),
        ("5.2", "Business continuity and disaster recovery of outsourced services"),
        ("6.1", "Data confidentiality, integrity and localisation"),
        ("6.2", "Incident reporting to the regulator"),
        ("7.1", "Concentration risk and exit strategy"),
        ("8.1", "Sub-contracting (fourth-party) controls"),
    ]),
}


def seed(conn, tenant_id: str) -> int:
    """Give a new organisation the baseline frameworks. Idempotent; never deletes.

    Frameworks are **tenant-owned copies** (the schema's MIGRATION note 7) rather than shared
    global rows: an organisation edits, retires and adds clauses to suit what it is actually
    being audited against, and one tenant's edits must never reach another's.

    `source='AUTHORED'` marks these as ours; anything a customer uploads is `'IMPORTED'`.
    """
    fw, fc = t("frameworks"), t("framework_clauses")
    existing = set(conn.execute(
        select(fw.c.code).where(fw.c.tenant_id == tenant_id)).scalars())
    now = now_iso()
    made = 0
    for code, (name, version, clauses) in BASELINE.items():
        if code in existing:
            continue
        fid = str(uuid.uuid4())
        conn.execute(insert(fw).values(
            id=fid, tenant_id=tenant_id, code=code, name=name, version=version,
            source="AUTHORED", created_at=now, updated_at=now))
        conn.execute(insert(fc), [
            {"id": str(uuid.uuid4()), "tenant_id": tenant_id, "framework_id": fid,
             "ref": ref, "title": title, "sort_order": i}
            for i, (ref, title) in enumerate(clauses)
        ])
        made += 1
    return made
