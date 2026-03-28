#!/bin/bash
# Starts the Copilot CLI HTTP proxy on port 3000.
# Run this on the host before starting the Docker bot.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROXY_JS="$SCRIPT_DIR/copilot_proxy_server.js"
PORT=3000

if lsof -ti tcp:$PORT > /dev/null 2>&1; then
  echo "Port $PORT is already in use. Proxy may already be running."
  exit 1
fi

echo "Starting Copilot CLI proxy on port $PORT..."
exec node "$PROXY_JS"
