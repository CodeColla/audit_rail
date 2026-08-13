"""The curated master control library, seeded into every organisation (P5-S9).

**These are OUR controls — the things this organisation does — not an external standard.**
The distinction matters and is the spine of the whole module: a `framework_clause` is what an
auditor *asks* ("ISO A.8.5", "SOC 2 CC6.1"); a `control` is what we *do*, written once in our
own words. `control_clause_map` joins them many-to-many, so one control can satisfy ISO, SOC 2
and an RBI clause simultaneously and the evidence is gathered once. Organising controls *per
certification* instead would duplicate "MFA on admin accounts" three times, with three owners
and three evidence trails — the mistake `db/schema.sql` calls out by name on
`control_clause_map` ("the explicit fix for Probo's single-framework Control").

Lifted out of `scripts/build_control_library.py`, which was a one-time bootstrap: it only ever
ran against the first install, so every organisation created through open signup got 16 empty
domains and **zero controls** — a Controls screen that was a dead end on day one. Exactly the
shape of the P4-S5 domains gap and the P5-S6 vocabularies gap.

That script still owns the destructive rebuild and the question→control keyword matching; it
now imports `F` from here so there is one copy of the curated set, not two that drift.
"""

from __future__ import annotations

import uuid

from sqlalchemy import insert, select

from api.core.database import t
from api.core.util import now_iso

# ── curated standard framework: code → [(ref, statement, lifecycle, months, kw)] ──
# lifecycle: one_time | recurring(+months) | per_audit ; kw = extra match hints
F: dict[str, list[tuple]] = {
    "GRP": [
        ("GRP 1.a", "Documented, management-approved information security policy", "one_time", None, "isp documented approved"),
        ("GRP 2.a", "Policies reviewed and re-approved at least annually", "recurring", 12, "review update annually"),
        ("GRP 3.a", "Process to approve exceptions to security policies", "per_audit", None, "exception waiver"),
        ("GRP 4.a", "Security organisation, roles and responsibilities (CISO/team)", "one_time", None, "ciso roles responsibilities dedicated team organization chart"),
        ("GRP 5.a", "Risk management program and periodic risk assessment", "recurring", 12, "risk assessment register management"),
        ("GRP 6.a", "Business impact assessment of critical assets", "recurring", 12, "bia impact critical assets"),
        ("GRP 7.a", "Acceptable use policy accepted by all staff", "recurring", 12, "acceptable use agreed employees"),
        ("GRP 8.a", "Alignment to a recognised framework (ISO 27001 / NIST)", "one_time", None, "framework nist iso standards"),
        ("GRP 9.a", "Senior leadership review of security implementation", "recurring", 12, "leadership review mom senior"),
        ("GRP 10.a", "Policy communicated to employees and contractors", "one_time", None, "communicate policy employees contractors"),
    ],
    "HR": [
        ("HR 1.a", "Background verification performed before hire", "per_audit", None, "background check verification hire"),
        ("HR 2.a", "Screening level (criminal/credit/reference) and records maintained", "per_audit", None, "criminal credit reference records"),
        ("HR 3.a", "NDA / confidentiality agreements signed by staff", "one_time", None, "nda non-disclosure confidentiality agreement"),
        ("HR 4.a", "Security awareness training program (periodic)", "recurring", 12, "awareness training program employees"),
        ("HR 5.a", "Role-specific / secure-coding training", "recurring", 12, "secure coding role training"),
        ("HR 6.a", "Access revocation on termination or role change", "per_audit", None, "termination revoke access leave"),
        ("HR 7.a", "Access controls during the notice period", "per_audit", None, "notice period access"),
        ("HR 8.a", "On-boarding / off-boarding policy and checklist", "one_time", None, "onboarding offboarding policy checklist"),
    ],
    "AM": [
        ("AM 1.a", "Joiner access provisioning on a role basis", "per_audit", None, "provision grant access new user role"),
        ("AM 2.a", "Role-based access control and least privilege enforced", "one_time", None, "rbac least privilege role based access"),
        ("AM 3.a", "Strong authentication (SSO / MFA)", "one_time", None, "authentication sso mfa oauth methods"),
        ("AM 4.a", "Strong password policy", "recurring", 12, "password policy strong enforce"),
        ("AM 5.a", "Privileged access management and monitoring", "recurring", 1, "privileged admin access pam monitored"),
        ("AM 6.a", "Periodic access recertification", "recurring", 3, "recertification review user access rights"),
        ("AM 7.a", "Unique IDs and segregation of duties", "one_time", None, "unique id segregation duties"),
        ("AM 8.a", "Timely revocation on leaver / role change", "per_audit", None, "revoke leaver access removal"),
        ("AM 9.a", "Restricted access to jump hosts / management functions", "one_time", None, "jump host virtualization administrative management"),
    ],
    "NI": [
        ("NI 1.a", "Approved network architecture diagram", "one_time", None, "network architecture diagram approved documented"),
        ("NI 2.a", "Network segmentation / segregation", "one_time", None, "segment segregation lateral movement vlan"),
        ("NI 3.a", "Perimeter controls (firewall / IDS / IPS)", "one_time", None, "firewall ids ips perimeter boundary"),
        ("NI 4.a", "Firewall / WAF / IDS-IPS rule review", "recurring", 3, "firewall waf rule review proxy"),
        ("NI 5.a", "Network traffic monitoring for anomalies", "recurring", 1, "monitor network traffic anomalies suspicious"),
        ("NI 6.a", "DDoS / WAF / CDN protection", "one_time", None, "ddos waf cdn protection"),
        ("NI 7.a", "Client data segregation on shared servers", "one_time", None, "segregated shared sftp client data server"),
        ("NI 8.a", "VA/PT of network & security devices, integrated with SIEM", "recurring", 6, "network devices vapt siem integrated"),
    ],
    "CS": [
        ("CS 1.a", "Cloud provider / data-centre inventory", "per_audit", None, "cloud provider data center which"),
        ("CS 2.a", "Cloud tenant data isolation", "per_audit", None, "tenant isolation data storage multi"),
        ("CS 3.a", "Cloud security posture management (CSPM)", "per_audit", None, "cspm posture monitor cloud"),
        ("CS 4.a", "Shared-responsibility agreements", "per_audit", None, "shared responsibility agreement documented"),
        ("CS 5.a", "Cloud logging, encryption and VAPT", "per_audit", None, "cloud logging encryption vapt patching"),
        ("CS 6.a", "Cloud tenant on/off-boarding management", "per_audit", None, "tenant onboarding offboarding management"),
    ],
    "AS": [
        ("AS 1.a", "Secure SDLC with security review gates", "one_time", None, "sdlc secure development lifecycle stages review"),
        ("AS 2.a", "Application inventory, components and architecture", "one_time", None, "application components framework architecture detail"),
        ("AS 3.a", "API security and API gateway", "one_time", None, "api gateway documented tested keys"),
        ("AS 4.a", "Secure coding guidelines followed", "one_time", None, "secure coding guidelines"),
        ("AS 5.a", "Application VAPT / source-code review (Cert-In)", "recurring", 6, "application security assessment source code review cert-in tested"),
        ("AS 6.a", "Third-party / open-source component management", "recurring", 3, "open-source third-party components patching"),
        ("AS 7.a", "Application privilege levels / admin access", "one_time", None, "privilege levels super admin application users"),
    ],
    "DP": [
        ("DP 1.a", "Data classification and handling policy", "one_time", None, "data classification handling policy sensitive"),
        ("DP 2.a", "Encryption in transit and at rest", "one_time", None, "encryption transit rest protect data"),
        ("DP 3.a", "Key / certificate management system", "one_time", None, "key management certificates keys"),
        ("DP 4.a", "Data retention schedule", "one_time", None, "retention period retained data"),
        ("DP 5.a", "Secure data disposal and deletion notification", "per_audit", None, "disposal wipe destruction deletion notified"),
        ("DP 6.a", "Privacy / consent for secondary use", "one_time", None, "privacy consent analytics marketing secondary"),
        ("DP 7.a", "Data residency / geolocation", "per_audit", None, "residency geographically stored data location"),
        ("DP 8.a", "DLP controls on endpoints handling data", "recurring", 1, "dlp data loss prevention endpoints"),
    ],
    "LM": [
        ("LM 1.a", "Centralised log collection (system/app/network)", "one_time", None, "logs collect system application network types"),
        ("LM 2.a", "Log retention and integrity", "recurring", 12, "log retention integrity maintained period"),
        ("LM 3.a", "SIEM / real-time security event monitoring", "recurring", 1, "siem real-time security event monitoring"),
        ("LM 4.a", "Alert / log review cadence", "recurring", 1, "logs alerts reviewed security team often"),
        ("LM 5.a", "Audit logs / activity reports available to clients", "per_audit", None, "audit logs activity reports customers"),
    ],
    "VP": [
        ("VP 1.a", "Vulnerability management policy and remediation SLAs", "recurring", 3, "vulnerability remediate policy technical identify"),
        ("VP 2.a", "Periodic penetration testing of infrastructure", "recurring", 3, "penetration test infrastructure routinely"),
        ("VP 3.a", "Patch management process and timelines", "recurring", 1, "patch management timelines evidence"),
        ("VP 4.a", "Secure configuration / hardening standards", "one_time", None, "secure configuration hardened standard build image"),
        ("VP 5.a", "Change management process", "per_audit", None, "change management request impact communicated"),
        ("VP 6.a", "Technology obsolescence management", "recurring", 12, "obsolescence unsupported unlicensed technology"),
    ],
    "IM": [
        ("IM 1.a", "Documented, tested incident response plan", "recurring", 12, "incident response plan tested documented"),
        ("IM 2.a", "Incident SLAs and client notification", "per_audit", None, "incident sla notification communication"),
        ("IM 3.a", "Incident tracker with RCA and lessons learned", "per_audit", None, "incident tracker rca lessons learned"),
        ("IM 4.a", "Awareness to identify security events", "recurring", 12, "awareness identify security events"),
    ],
    "BC": [
        ("BC 1.a", "Documented business continuity plan", "one_time", None, "documented business continuity bcp plan critical"),
        ("BC 2.a", "BCP / DR testing with results (periodic)", "recurring", 12, "bcp dr tested last time results frequency"),
        ("BC 3.a", "Backups performed per policy", "recurring", 1, "backup data performed policy"),
        ("BC 4.a", "RTO / RPO defined", "one_time", None, "rto rpo recovery objective"),
        ("BC 5.a", "Redundancy / high availability / uptime SLA", "one_time", None, "redundancy failover high availability uptime sla"),
        ("BC 6.a", "Offsite / cross-region backup", "recurring", 1, "offsite cross-region backup where"),
    ],
    "PE": [
        ("PE 1.a", "Physical security controls (biometric / CCTV)", "one_time", None, "physical security biometric cctv controls"),
        ("PE 2.a", "Visitor management and access logs", "recurring", 1, "visitor management physical access logs"),
        ("PE 3.a", "Environmental controls (UPS / fire suppression)", "recurring", 12, "ups fire suppression environmental power"),
        ("PE 4.a", "Data-centre location and ownership", "per_audit", None, "physical data center located owned leased"),
    ],
    "TP": [
        ("TP 1.a", "Vendor / outsourcing risk management policy", "one_time", None, "outsourcing risk management vendor policy framework"),
        ("TP 2.a", "Fourth-party subcontracting disclosure and controls", "per_audit", None, "sub-contract 4th party fourth supply"),
        ("TP 3.a", "Supply-chain assurance reviews", "recurring", 12, "supply chain assurance reports reviewed"),
    ],
    "LR": [
        ("LR 1.a", "Regulatory / statutory compliance monitoring", "recurring", 12, "regulatory statutory compliance requirements"),
        ("LR 2.a", "Certifications held (ISO 27001 / SOC 2 / STQC)", "recurring", 12, "certification iso soc stqc standards hold"),
        ("LR 3.a", "Cyber-security insurance", "recurring", 12, "cyber insurance liability"),
        ("LR 4.a", "Ethics / anti-corruption controls", "one_time", None, "ethics corruption compliance"),
    ],
    "BF": [
        ("BF 1.a", "Financial stability / revenue trend", "recurring", 12, "financial revenue credit trend"),
        ("BF 2.a", "Business justification for the engagement", "per_audit", None, "business justification use vendor"),
        ("BF 3.a", "Customer complaint / grievance handling", "per_audit", None, "customer complaint grievance resolved client"),
    ],
    "AI": [
        ("AI 1.a", "AI/ML tool scope and inventory", "per_audit", None, "ai ml tool scope model language"),
        ("AI 2.a", "AI threat modeling and risk assessment", "per_audit", None, "ai threat modeling risk assessment"),
        ("AI 3.a", "AI training-data governance", "per_audit", None, "ai training data sources model"),
        ("AI 4.a", "AI security testing", "per_audit", None, "ai security testing source code vulnerability"),
    ],
}


def seed(conn, tenant_id: str) -> int:
    """Give a new organisation the curated control library. Idempotent, and NEVER deletes.

    Additive by construction: `controls` is real editable data the moment a customer touches
    it — `stock_response` answers, linked evidence, attached policies, tasks — so a seed that
    cleared first would destroy work it did not create. `scripts/build_control_library.py`
    keeps the destructive rebuild, behind its own `--force` guard.

    Everything lands `applicable`. The script marks the `CS` and `AI` domains dormant because
    *KIAM* is an on-prem vendor with no cloud or AI footprint — a fact about one customer, not
    a default for everyone. Deciding what does not apply, with a justification, is the
    Statement of Applicability's job, not the seed's.
    """
    c = t("controls")
    domain_id = {code: cid for cid, code in conn.execute(
        select(t("domains").c.id, t("domains").c.code)
        .where(t("domains").c.tenant_id == tenant_id))}
    existing = set(conn.execute(
        select(c.c.code).where(c.c.tenant_id == tenant_id)).scalars())

    now = now_iso()
    rows = []
    for domain_code, defs in F.items():
        did = domain_id.get(domain_code)
        # `controls.domain_id` is NOT NULL with a composite FK, so a missing domain would be
        # an IntegrityError mid-signup. Skip rather than fail: domains.seed() runs first, and
        # a tenant that somehow lacks one should still get the rest of its library.
        if did is None:
            continue
        for ref, statement, lifecycle, months, _kw in defs:
            if ref in existing:
                continue
            rows.append({
                "id": str(uuid.uuid4()), "tenant_id": tenant_id, "domain_id": did,
                "code": ref, "statement": statement, "lifecycle": lifecycle,
                "recurrence_months": months, "applicability": "applicable",
                "status": "active", "created_at": now, "updated_at": now,
            })
    if rows:
        conn.execute(insert(c), rows)
    return len(rows)
