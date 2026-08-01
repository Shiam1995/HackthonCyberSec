#!/usr/bin/env bash
# HackthonCyberSec (I AM NIGERIAN PRINCE) — hackathon launcher.
#   ./run.sh          start everything, open the pitch page
#   ./run.sh console  open the operator console instead
#   ./run.sh stop     shut down
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

case "${1:-start}" in
  stop)  exec engine/demo.sh stop ;;
  status) exec engine/demo.sh status ;;
esac

engine/demo.sh restart >/dev/null 2>&1 &
BOOT=$!
printf "starting (loading whisper + XTTS + ollama, ~25s)"
for _ in $(seq 1 60); do
    curl -s --max-time 2 http://127.0.0.1:8080/health >/dev/null 2>&1 && break
    printf '.'; sleep 1
done
wait $BOOT 2>/dev/null || true
echo

TARGET="http://127.0.0.1:8080/pitch"
[ "${1:-}" = "console" ] && TARGET="http://127.0.0.1:8080/"

cat <<BANNER

  ┌──────────────────────────────────────────────────┐
  │  PITCH    http://127.0.0.1:8080/pitch            │
  │  CONSOLE  http://127.0.0.1:8080/                 │
  └──────────────────────────────────────────────────┘

  Demo:  HEAR MY VOICE  →  TAKE OVER  →  START CALL
         →  pick character, set 25s  →  START MORPH

BANNER

for o in xdg-open firefox google-chrome chromium; do
    command -v "$o" >/dev/null 2>&1 && { "$o" "$TARGET" >/dev/null 2>&1 & break; }
done
