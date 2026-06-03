#!/bin/bash
# ─────────────────────────────────────────────────────────────────
#  Momentum Builder — One-Click Start Script
#  Run this file to start the app + permanent public URL
# ─────────────────────────────────────────────────────────────────

# ⬇️  PASTE YOUR NGROK STATIC DOMAIN HERE (from dashboard.ngrok.com/domains)
NGROK_DOMAIN="apprehend-rebuilt-thesaurus.ngrok-free.dev"

PYTHON="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14"
APP_DIR="/Users/amjad.mohammad/Documents/Claude/Projects/Momentum Builder"
PORT=5001

echo ""
echo "========================================================"
echo "  🚀  Momentum Builder — Starting Up"
echo "========================================================"

# Prevent Mac from sleeping while app is running
echo "  ☕  Keeping Mac awake (caffeinate)..."
caffeinate -i &
CAFE_PID=$!

# Kill anything already running on the port
lsof -ti:$PORT | xargs kill -9 2>/dev/null
sleep 1

# Start Flask
echo "  ▶  Starting Flask app on port $PORT..."
cd "$APP_DIR"
$PYTHON -c "
import app as a
a.app.run(debug=False, host='0.0.0.0', port=$PORT)
" &> /tmp/momentum_flask.log &
FLASK_PID=$!

# Wait for Flask to be ready
sleep 4
if curl -s "http://localhost:$PORT/api/health" | grep -q "ok"; then
    echo "  ✅  Flask is running (PID $FLASK_PID)"
else
    echo "  ❌  Flask failed to start. Check /tmp/momentum_flask.log"
    exit 1
fi

# Start ngrok tunnel
echo "  ▶  Starting permanent tunnel..."
echo ""
echo "========================================================"
echo "  🌐  YOUR PERMANENT URL:"
echo "  👉  https://$NGROK_DOMAIN"
echo ""
echo "  Open this on any device — phone, tablet, anywhere."
echo "  URL never changes."
echo ""
echo "  Press Ctrl+C to stop everything."
echo "========================================================"
echo ""

ngrok http --url="$NGROK_DOMAIN" $PORT

# When Ctrl+C is pressed — clean up everything
echo ""
echo "  🛑  Shutting down..."
kill $CAFE_PID 2>/dev/null
lsof -ti:$PORT | xargs kill -9 2>/dev/null
echo "  ✅  All stopped. Mac sleep restored."
