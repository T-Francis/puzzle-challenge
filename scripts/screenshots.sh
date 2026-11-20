#!/bin/sh
# README screenshots (docs/images/atelier_en.png, demo.jpg, catalog.png), at the repository root:
#   sh scripts/screenshots.sh
# Starts a throwaway workshop (empty journal in memory, port 8081), takes the screenshots with headless Chromium
# (Playwright image), stops the workshop. The other README images: src/illustrations.py.
set -e
cd "$(dirname "$0")/.."
PW=mcr.microsoft.com/playwright:v1.48.2-jammy
docker build -q --target server -t puzzle-server:dev . >/dev/null
docker rm -f puzzle-shot >/dev/null 2>&1 || true
docker run -d --rm --name puzzle-shot -p 127.0.0.1:8081:8080 --tmpfs /data:uid=1000,gid=1000 \
  -e PUZZLE_DEV_AUTOLOGIN=1 -e PUZZLE_ORIGIN=http://localhost:8081 -e PUZZLE_COOKIE_SECURE=0 puzzle-server:dev >/dev/null
trap 'docker stop puzzle-shot >/dev/null 2>&1 || true' EXIT
echo "waiting for the workshop…"
for _ in $(seq 1 100); do curl -s http://localhost:8081/api/state | grep -q '"status":"ready"' && break; sleep 3; done
docker run --rm --network host -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$(pwd):/repo:ro" -v "$(pwd)/docs/images:/out" "$PW" \
  sh -c 'cd /tmp && npm i -s playwright@1.48.2 >/dev/null 2>&1 && cp /repo/scripts/screenshots.mjs . && node screenshots.mjs'
