#!/bin/sh
set -eu
echo "Starting BgUtils PO-token provider..."
cd /opt/bgutil/server
deno run -A src/main.ts --port 4416 > /tmp/bgutil.log 2>&1 &
POT_PID=$!
sleep 2
if ! kill -0 "$POT_PID" 2>/dev/null; then
  echo "BgUtils provider failed to start:"
  cat /tmp/bgutil.log || true
  exit 1
fi
echo "PO-token provider running on 127.0.0.1:4416"
cd /app
exec uvicorn main:app --host 0.0.0.0 --port "${PORT}"
