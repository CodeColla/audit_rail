"""M3 — policy register, versioned upload, and review roll-forward."""

import datetime as dt

from tests.conftest import token


def test_policy_lifecycle(app_client):
    tok = token(app_client, "member@kiam.example", "secret2")
    h = {"Authorization": f"Bearer {tok}"}

    # create a policy already overdue for review
    r = app_client.post("/api/policies", headers=h, json={
        "title": "Information Security Policy", "review_cadence_months": 12,
        "next_review_at": "2020-01-01"})
    assert r.status_code == 201
    pid = r.json()["id"]

    listed = app_client.get("/api/policies", headers=h).json()
    pol = next(p for p in listed if p["id"] == pid)
    assert pol["review_status"] == "overdue"

    # add a version with a file — should roll the review date forward
    r = app_client.post(f"/api/policies/{pid}/versions", headers=h,
        data={"version_label": "v3.0", "effective_from": "2026-07-11"},
        files={"file": ("ISP_v3.pdf", b"%PDF policy body", "application/pdf")})
    assert r.status_code == 201, r.text
    new_review = r.json()["next_review_at"]
    assert new_review is not None and new_review > "2026-07-11"

    detail = app_client.get(f"/api/policies/{pid}", headers=h).json()
    assert detail["review_status"] == "ok"
    assert detail["versions"][0]["version_label"] == "v3.0"
    assert detail["versions"][0]["original_name"] == "ISP_v3.pdf"

    # explicit review advances by the cadence (~12 months from today)
    r = app_client.post(f"/api/policies/{pid}/review", headers=h).json()
    expected = (dt.date.today() + dt.timedelta(days=360))
    assert dt.date.fromisoformat(r["next_review_at"]) >= expected

    # delete requires manager/admin
    assert app_client.delete(f"/api/policies/{pid}", headers=h).status_code == 403
    tok_admin = token(app_client, "admin@kiam.example", "secret1")
    r = app_client.delete(f"/api/policies/{pid}",
                          headers={"Authorization": f"Bearer {tok_admin}"})
    assert r.status_code == 204
