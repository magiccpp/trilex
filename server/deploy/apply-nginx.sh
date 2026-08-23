#!/usr/bin/env bash
# Run ON the server. Backs up, inserts the block, validates, reloads.
# Restores the backup automatically if nginx rejects the new config.
set -euo pipefail
CONF=/home/ken/git/stock-orch/nginx/conf.d/default.conf
BAK="${CONF}.bak.$(date +%Y%m%d-%H%M%S)"

cp -a "$CONF" "$BAK"; echo "backup: $BAK"

python3 - "$CONF" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1]); s = p.read_text()
if "/thriauga/" in s:
    print("already present; nothing to do"); raise SystemExit
block = """    location /thriauga/ {
        proxy_pass http://thriauga-sync:8000/;
        proxy_http_version 1.1;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_connect_timeout 10s;
        proxy_read_timeout    60s;
        client_max_body_size  8m;
    }

"""
i = s.index("    location /api/ {")     # inside the xiaodong.io :443 block
p.write_text(s[:i] + block + s[i:])
print("inserted location /thriauga/")
PY

# nginx needs to share a network with the API container to resolve its name.
docker network connect thriauga_internal xiaodong-nginx 2>/dev/null || echo "(network already connected)"

if docker exec xiaodong-nginx nginx -t; then
    docker exec xiaodong-nginx nginx -s reload
    echo "reloaded with no downtime"
    curl -sk -o /dev/null -w "https://xiaodong.io/thriauga/v1/health -> %{http_code}\n" \
        https://127.0.0.1/thriauga/v1/health -H 'Host: xiaodong.io'
else
    echo "CONFIG INVALID - restoring backup"; cp -a "$BAK" "$CONF"; exit 1
fi
