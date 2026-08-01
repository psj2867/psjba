#!/bin/bash
# Quick CDP connectivity test for chromium
set -e

rm -rf /tmp/chrome-final
pkill -f "chromium.*remote-debugging" 2>/dev/null || true
sleep 1

/usr/bin/chromium --headless=new --no-sandbox --disable-gpu \
  --no-first-run --disable-extensions --enable-unsafe-swiftshader \
  --in-process-gpu --remote-debugging-port=9235 --remote-allow-origins=* \
  --user-data-dir=/tmp/chrome-final --disable-dev-shm-usage \
  > /tmp/chrome-final.log 2>&1 &
C=$!
echo "PID=$C"

for i in $(seq 1 20); do
  if curl -s --max-time 1 http://127.0.0.1:9235/json/version > /dev/null 2>&1; then
    echo "CDP OK after ${i}s"
    echo "Version: $(curl -s --max-time 2 http://127.0.0.1:9235/json/version | python3 -c "import sys,json; print(json.load(sys.stdin).get('Browser','?'))" 2>/dev/null)"
    break
  fi
  sleep 1
done

kill -0 $C 2>/dev/null && echo "Chromium alive" || echo "Chromium DEAD"
kill $C 2>/dev/null || true
wait $C 2>/dev/null || true
echo "=== Done ==="