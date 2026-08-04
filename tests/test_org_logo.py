"""P6 — the organisation logo.

Sumit, taking the product to public launch: *"the org name on the top Right does not
highlight a lot, maybe an Org Icon/Photo provision we should give."*

Most of what is worth testing here is what the route REFUSES. A logo is rendered inside the
authenticated app on every screen, so an upload path is a small but real attack surface.
"""

import uuid

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
JPEG = (b"\xff\xd8\xff\xe0" + b"\x00" * 64)
WEBP = (b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 64)


def _new_org(client, tag):
    r = client.post("/api/auth/signup", json={
        "full_name": f"{tag} owner", "email": f"{tag}@example.com",
        "password": "Passw0rdOne", "organisation_name": f"Org {tag}"})
    assert r.status_code == 201, r.text
    j = r.json()
    return j, {"Authorization": f"Bearer {j['access_token']}"}


def test_a_logo_can_be_set_fetched_and_cleared(app_client):
    _org, h = _new_org(app_client, f"logo{uuid.uuid4().hex[:6]}")
    # 204, not 404: "no logo" is a successful answer, and most organisations never set one —
    # a 404 here meant a failed request on every page load of the whole product.
    assert app_client.get("/api/org/logo", headers=h).status_code == 204

    up = app_client.put("/api/org/logo", headers=h,
                        files={"file": ("mark.png", PNG, "image/png")})
    assert up.status_code == 200, up.text
    assert up.json()["mime_type"] == "image/png"

    got = app_client.get("/api/org/logo", headers=h)
    assert got.status_code == 200
    assert got.headers["content-type"].startswith("image/png")
    assert got.content == PNG
    assert "inline" in got.headers["content-disposition"]

    assert app_client.delete("/api/org/logo", headers=h).status_code == 200
    assert app_client.get("/api/org/logo", headers=h).status_code == 204


def test_an_svg_is_refused_with_a_reason(app_client):
    """An SVG is executable markup and this image renders inside the app on every screen.
    A logo does not need vector fidelity, so the safe answer is simply no — said out loud,
    rather than silently dropping the file."""
    _org, h = _new_org(app_client, f"svg{uuid.uuid4().hex[:6]}")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    r = app_client.put("/api/org/logo", headers=h,
                       files={"file": ("logo.svg", svg, "image/svg+xml")})
    assert r.status_code == 400
    assert "svg" in r.text.lower()
    assert app_client.get("/api/org/logo", headers=h).status_code == 204


def test_the_bytes_decide_the_format_not_the_filename(app_client):
    """Filename and browser-supplied Content-Type are both attacker-controlled. A payload
    dressed up as a .png must still be refused, and a real image with a silly name accepted."""
    _org, h = _new_org(app_client, f"sniff{uuid.uuid4().hex[:6]}")

    liar = app_client.put("/api/org/logo", headers=h,
                          files={"file": ("logo.png", b"<html>not an image</html>", "image/png")})
    assert liar.status_code == 400

    honest = app_client.put("/api/org/logo", headers=h,
                            files={"file": ("whatever.bin", JPEG, "application/octet-stream")})
    assert honest.status_code == 200, honest.text
    assert honest.json()["mime_type"] == "image/jpeg", "the sniffed type wins"

    webp = app_client.put("/api/org/logo", headers=h,
                          files={"file": ("m.webp", WEBP, "image/webp")})
    assert webp.status_code == 200 and webp.json()["mime_type"] == "image/webp"


def test_an_oversize_or_empty_logo_is_refused(app_client):
    from api.routers.org import MAX_LOGO_MB

    _org, h = _new_org(app_client, f"big{uuid.uuid4().hex[:6]}")
    big = PNG + b"\x00" * (MAX_LOGO_MB * 1024 * 1024)
    assert app_client.put("/api/org/logo", headers=h,
                          files={"file": ("big.png", big, "image/png")}).status_code == 413
    assert app_client.put("/api/org/logo", headers=h,
                          files={"file": ("empty.png", b"", "image/png")}).status_code == 400


def test_only_org_editors_can_change_the_logo_but_everyone_sees_it(app_client):
    from tests.test_rbac import _member_with_role

    org, h = _new_org(app_client, f"perm{uuid.uuid4().hex[:6]}")
    app_client.put("/api/org/logo", headers=h, files={"file": ("m.png", PNG, "image/png")})

    # Changing it is an admin action; SEEING it is not. `org` is one of ADMIN_MODULES, so
    # gating the read on `org.view` would have meant only administrators ever saw the company
    # logo and every other member got a 403 and an initials box in the header.
    for role in ("Viewer", "Editor"):
        hh, _ = _member_with_role(app_client, org["tenant_id"], role)
        assert app_client.put("/api/org/logo", headers=hh,
                              files={"file": ("x.png", PNG, "image/png")}).status_code == 403, role
        assert app_client.delete("/api/org/logo", headers=hh).status_code == 403, role
        seen = app_client.get("/api/org/logo", headers=hh)
        assert seen.status_code == 200, f"{role} must still see the header logo"
        assert seen.content == PNG


def test_a_logo_never_leaks_between_organisations(app_client):
    """`tenants.logo_file_id` references `files (id, tenant_id)`, so the DATABASE refuses a
    cross-tenant pointer — this route is not trusted to get it right on its own."""
    a, ha = _new_org(app_client, f"lka{uuid.uuid4().hex[:6]}")
    b, hb = _new_org(app_client, f"lkb{uuid.uuid4().hex[:6]}")
    app_client.put("/api/org/logo", headers=ha, files={"file": ("a.png", PNG, "image/png")})

    assert app_client.get("/api/org/logo", headers=hb).status_code == 204

    # and the composite FK physically refuses the cross-tenant assignment
    from sqlalchemy import text as sql

    from api.database import engine
    with engine.connect() as c:
        fid = c.execute(sql("SELECT logo_file_id FROM tenants WHERE id = :t"),
                        {"t": a["tenant_id"]}).scalar()
    assert fid
    import sqlalchemy.exc
    try:
        with engine.begin() as c:
            c.execute(sql("UPDATE tenants SET logo_file_id = :f WHERE id = :t"),
                      {"f": fid, "t": b["tenant_id"]})
        raise AssertionError("the composite FK must refuse another tenant's file")
    except sqlalchemy.exc.IntegrityError:
        pass
