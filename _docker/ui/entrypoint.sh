#!/bin/sh
# Container entrypoint for the UI image: generate the runtime config, then become nginx.
#
# Same shape as GINTI's webui entrypoint — write config from the environment, then
# `exec nginx -g "daemon off;"` — so API_URL is a RUNTIME choice, not a build-time one. Edit it
# in Portainer on a running container, restart, done. Nothing is baked into the JavaScript.
#
# What differs from GINTI is WHAT gets written. GINTI emits JavaScript (window.__env__) and
# lets axios hold an absolute, cross-origin base URL. This app cannot: the browser has to see
# ONE origin, because
#
#   * the public signing page calls fetch("/api/sign/<token>") with a hardcoded root-relative
#     path and never touches the axios instance (webui/src/pages/auth/Sign.tsx),
#   * the org logo, evidence previews and asset photos are fetched as blobs carrying a bearer
#     header, which an <img src> cannot do (components/Avatar.tsx, FilePreview.tsx), and
#   * published document HTML contains literal src="/api/documents/images/<uuid>" strings, and
#     document_versions.content_sha256 is GENERATED ALWAYS over that exact content, frozen by
#     freeze_published_version() and signed into electronic_signatures. Those URLs can never be
#     rewritten — one changed byte invalidates every signature on the document.
#
# Forwarding at the edge keeps all three true. Injecting a different origin into the bundle
# would break the first two and permanently corrupt the third.
set -eu

TARGET_DIR=/etc/nginx/api-proxy
CONF="$TARGET_DIR/api.conf"

mkdir -p "$TARGET_DIR"
rm -f "$CONF"

if [ -z "${API_URL:-}" ]; then
  # nginx.conf includes this directory with a glob, so no file simply means no /api block —
  # which is the correct behaviour when a reverse proxy in front already routes /api.
  echo "[api-proxy] API_URL not set — /api is left to the reverse proxy in front."
else
  # Trailing slashes matter here and get it wrong silently. `proxy_pass` with NO path component
  # forwards the URI unchanged, so /api/documents stays /api/documents — which is right, because
  # the FastAPI routers are mounted AT /api (api/main.py). Add a trailing slash and nginx strips
  # the prefix and every route 404s.
  UPSTREAM=$(printf '%s' "$API_URL" | sed 's:/*$::')

  cat > "$CONF" <<EOF
location /api/ {
    proxy_pass         $UPSTREAM;

    proxy_http_version 1.1;
    proxy_set_header   Host              \$host;
    proxy_set_header   X-Real-IP         \$remote_addr;
    proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
    proxy_set_header   X-Forwarded-Proto \$scheme;

    # Uploads run to 25 MB (evidence files). nginx defaults to 1 MB and rejects the rest with
    # an opaque HTML error page the SPA cannot explain — one of the failures that looks like a
    # bug in the app rather than a setting.
    client_max_body_size 30m;

    # A draft PDF is rendered synchronously inside the request by xhtml2pdf; 60s is close for
    # a long policy.
    proxy_connect_timeout 10s;
    proxy_send_timeout   120s;
    proxy_read_timeout   120s;

    # Stream downloads rather than spooling a 25 MB evidence file to disk first.
    proxy_buffering off;
}
EOF

  echo "[api-proxy] /api -> $UPSTREAM"
fi

exec nginx -g "daemon off;"
