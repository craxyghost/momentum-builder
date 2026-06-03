#!/bin/bash
# ─────────────────────────────────────────────────────────
# Momentum Builder — Quick Start Script
# ─────────────────────────────────────────────────────────

cd "$(dirname "$0")"

echo ""
echo "════════════════════════════════════════════"
echo "  📈  Momentum Builder — Starting up..."
echo "════════════════════════════════════════════"

# Install/update dependencies
echo ""
echo "  → Checking dependencies..."
pip3 install -r requirements.txt -q --break-system-packages 2>/dev/null || \
pip3 install -r requirements.txt -q

echo "  → Dependencies ready."
echo ""
echo "  → Starting server at http://127.0.0.1:5000"
echo "  → Press Ctrl+C to stop."
echo "════════════════════════════════════════════"
echo ""

python3 app.py
