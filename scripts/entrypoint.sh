#!/bin/bash
set -e

# ---------------------------------------------------------------------------
# Validate required environment
# ---------------------------------------------------------------------------
if [ -z "$TAX_YEAR" ]; then
    echo "ERROR: TAX_YEAR environment variable is required."
    echo ""
    echo "Usage:"
    echo "  podman run -it --rm -e TAX_YEAR=2025 -v ./data:/data/taxes/data:Z -p 8000:8000 tax-processor"
    exit 1
fi

PRIOR_YEAR=$((TAX_YEAR - 1))

# ---------------------------------------------------------------------------
# Ensure data directory structure exists (mount may be empty on first run)
# ---------------------------------------------------------------------------
mkdir -p \
    data/raw/"$TAX_YEAR"/sources \
    data/raw/"$TAX_YEAR"/filed \
    data/raw/"$TAX_YEAR"/knowledge \
    data/raw/"$PRIOR_YEAR"/sources \
    data/raw/"$PRIOR_YEAR"/filed \
    data/raw/"$PRIOR_YEAR"/knowledge \
    data/extracted \
    data/sanitized \
    data/vault \
    data/instructions \
    data/output/"$TAX_YEAR"

# ---------------------------------------------------------------------------
# Run inventory scan to generate dashboard
# ---------------------------------------------------------------------------
echo "Running inventory for tax year $TAX_YEAR..."
python3 scripts/inventory.py --year "$TAX_YEAR"

# ---------------------------------------------------------------------------
# Generate dashboard credentials
# ---------------------------------------------------------------------------
DASHBOARD_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")
export DASHBOARD_PASSWORD

# ---------------------------------------------------------------------------
# Start dashboard server in background
# ---------------------------------------------------------------------------
DASHBOARD_PORT="${DASHBOARD_PORT:-8000}"
echo "Starting dashboard server on port $DASHBOARD_PORT..."
python3 scripts/serve_dashboard.py --host 0.0.0.0 --port "$DASHBOARD_PORT" --auth &

echo ""
echo "============================================"
echo "  Tax Processor Ready"
echo "  Tax Year: $TAX_YEAR"
echo "  Dashboard: http://localhost:$DASHBOARD_PORT"
echo "  Username:  admin"
echo "  Password:  $DASHBOARD_PASSWORD"
echo "============================================"
echo ""
echo "Commands:"
echo "  python3 scripts/orchestrate.py --year $TAX_YEAR"
echo "  python3 scripts/extract.py --help"
echo "  python3 scripts/sanitize.py --help"
echo "  python3 scripts/process.py --help"
echo "  python3 scripts/assemble.py --help"
echo "  python3 scripts/inventory.py --year $TAX_YEAR"
echo ""

# ---------------------------------------------------------------------------
# If arguments were passed, run them instead of interactive shell
# ---------------------------------------------------------------------------
if [ $# -gt 0 ]; then
    exec "$@"
fi

# Drop to interactive bash
exec bash
