"""Shared, stdlib-only text→control mapping helpers (M2 build + M6 importer).

Keeps the domain classifier and the token-overlap matcher in one place so the
offline library builder and the live import wizard agree.
"""

from __future__ import annotations

import re

# keyword → domain code (specific domains first; ties broken by this order)
DOMAIN_RULES: list[tuple[str, list[str]]] = [
    ("AI", ["ai/ml", "machine learning", " ml ", "llm", "large language", "chatgpt",
            "openai", "model", "generative"]),
    ("CS", ["cloud", "tenant", "saas", "paas", "iaas", "csp", "cspm",
            "shared responsib"]),
    ("BC", ["business continuity", "bcp", "disaster recovery", " dr ", " dr.", "rto",
            "rpo", "backup", "redundan", "failover", "high availability", "uptime"]),
    ("IM", ["incident response", "incident management", "incident tracker", "breach",
            "root cause", " rca", "security event", "crisis"]),
    ("PE", ["physical", "cctv", "biometric", "visitor", "environmental", "ups",
            "fire suppression", "data center", "premises", "badge", "access card"]),
    ("AM", ["access control", "access management", "authentication", "password",
            " mfa", "sso", "privilege", "rbac", "identity", "credential", "joiner",
            "leaver", "recertif", "unique id", "least privilege", "jump host",
            "administrative"]),
    ("NI", ["network", "firewall", "segment", "vlan", "perimeter", " ids", " ips",
            " waf", "router", "ddos", "proxy", "infrastructure", "segregation", "cdn",
            "lateral movement"]),
    ("AS", ["application security", "sdlc", "source code", " api", "secure coding",
            "software development", "owasp", "web application"]),
    ("VP", ["vulnerability", "patch", "vapt", "penetration", "scan", "hardening",
            "configuration management", "change management", "change request",
            "obsolescence", "standard build"]),
    ("DP", ["data classification", "privacy", "encryption", "encrypt", "retention",
            "disposal", " dlp", "personal data", "consent", "key management",
            "at rest", "in transit", "data protection", "records management"]),
    ("LM", ["logging", " log ", "logs", "siem", "monitoring", "alert", "audit log",
            "detection", " soc "]),
    ("HR", ["employee", "background check", "background verification", "personnel",
            "awareness", "training", "onboarding", "on-boarding", "termination",
            "screening", "human resource", "staff", "off-boarding"]),
    ("TP", ["third party", "third-party", "subcontract", "sub-contract",
            "supply chain", "4th party", "fourth party", "outsourc"]),
    ("LR", ["compliance", "regulatory", "regulation", "statutory", "insurance",
            "certification", "iso 27001", "legal", "ethics", "corruption", "stqc"]),
    ("BF", ["financial", "revenue", "credit", "business justification",
            "customer complaint", "grievance", "client control"]),
    ("GRP", ["policy", "governance", "risk assessment", "risk management",
             "management approval", "roles and responsib", "organization", "ciso",
             "framework", "standard", "approved by management", "business impact",
             "exception"]),
]

_STOP = set("do you have a an the is are of to and or in on for with your our we that "
            "which any all as per please provide share describe kindly how what when "
            "where within been has your".split())


def toks(text: str) -> set[str]:
    text = re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())
    return {w for w in text.split() if w not in _STOP and len(w) > 2}


def classify(text: str, section: str = "") -> str:
    blob = f" {(text or '').lower()} {(section or '').lower()} "
    best, best_score, best_rank = "GRP", 0, 999
    for rank, (code, kws) in enumerate(DOMAIN_RULES):
        score = sum(1 for kw in kws if kw in blob)
        if score > best_score or (score == best_score and score > 0 and rank < best_rank):
            best, best_score, best_rank = code, score, rank
    return best


def overlap(a: set[str], b: set[str]) -> float:
    """Overlap coefficient — rewards a question containing a control's key terms."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def best_control(question_text: str, candidates: list[tuple[str, set[str]]]):
    """candidates = [(control_id, control_token_set)]; returns (control_id, score)."""
    qt = toks(question_text)
    best_id, best_score = None, -1.0
    for cid, ctoks in candidates:
        s = overlap(qt, ctoks)
        if s > best_score:
            best_id, best_score = cid, s
    return best_id, round(max(best_score, 0.0), 3)
