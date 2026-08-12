#!/bin/sh
# Runs before nginx starts (the official nginx image executes /docker-entrypoint.d/*).
#
# Writes an /api forwarding block ONLY when API_URL is set. That makes the API location a
# RUNTIME choice: stop the container in Portainer, set or clear API_URL, start it again.
#
# Why nginx config and not a JavaScript variable: the SPA calls the API with a relative
# `baseURL: "/api"` (webui/src/lib/api.ts), and several things depend on the browser seeing
# ONE origin — the public signing page fetches `/api/sign/...` with a hardcoded root-relative
# path, its policy images are native <img src="/api/sign/…"> tags that carry no bearer token,
# and stored document HTML contains literal `/api/documents/images/<uuid>` srcs that are
# folded into content_sha256 and therefore cannot be rewritten. Forwarding at the edge keeps
# all of that true. Injecting a different origin into the JS would not.
set -eu

TARGET_DIR=/etc/nginx/api-proxy
CONF="$TARGET_DIR/api.conf"

mkdir -p "$TARGET_DIR"
rm -f "$CONF"

if [ -z "${API_URL:-}" ]; then
  echo "[api-proxy] API_URL not set — /api is left to the reverse proxy in front."
  exit 0
fi

# Trailing slashes matter and get this wrong silently. `proxy_pass` with NO path component
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
