#!/usr/bin/env bash
# deploy.sh — deploy report-needs MCP server to VPS
# Run from: /Users/dembe/Documents/AI/report-needs/
# Prerequisites: ssh-add ~/.ssh/id_ed25519  (unlock key first)

set -euo pipefail

VPS="root@157.230.82.223"
REMOTE_DIR="/opt/report-needs"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Step 1: Check SSH access ==="
ssh "$VPS" 'uname -a'

echo ""
echo "=== Step 2: Check what's running ==="
ssh "$VPS" 'ls /opt/ 2>/dev/null; echo "---"; ps aux | grep python | grep -v grep || echo "(no python processes)"'

echo ""
echo "=== Step 3: Check existing Caddy config ==="
ssh "$VPS" 'cat /etc/caddy/Caddyfile 2>/dev/null || echo "(no Caddyfile found)"'

echo ""
echo "=== Step 4: Create deployment directory ==="
ssh "$VPS" "mkdir -p $REMOTE_DIR"

echo ""
echo "=== Step 5: Copy files ==="
scp "$LOCAL_DIR/server.py" "$VPS:$REMOTE_DIR/server.py"
scp "$LOCAL_DIR/requirements.txt" "$VPS:$REMOTE_DIR/requirements.txt"

echo ""
echo "=== Step 6: Set up Python venv and install dependencies ==="
ssh "$VPS" bash << 'REMOTE'
set -euo pipefail
cd /opt/report-needs

python3 --version

if [ ! -d venv ]; then
    python3 -m venv venv
    echo "venv created"
else
    echo "venv already exists"
fi

venv/bin/pip install --quiet --upgrade pip
venv/bin/pip install --quiet -r requirements.txt
echo "Dependencies installed:"
venv/bin/pip show mcp | grep -E "^(Name|Version):"
REMOTE

echo ""
echo "=== Step 7: Install systemd service ==="
scp "$LOCAL_DIR/report-needs.service" "$VPS:/etc/systemd/system/report-needs.service"
ssh "$VPS" bash << 'REMOTE'
systemctl daemon-reload
systemctl enable report-needs
systemctl restart report-needs
sleep 3
systemctl status report-needs --no-pager
REMOTE

echo ""
echo "=== Step 8: Verify port 8000 is listening on localhost ==="
ssh "$VPS" 'ss -tlnp | grep 8000 || echo "WARNING: port 8000 not yet listening (give it a few seconds)"'

echo ""
echo "=== Step 9: Test MCP endpoint locally on VPS ==="
ssh "$VPS" 'curl -s -o /dev/null -w "HTTP %{http_code}" http://127.0.0.1:8000/mcp 2>&1 || echo "endpoint not reachable yet"'

echo ""
echo "=== Step 10: Update Caddy to proxy /mcp ==="
ssh "$VPS" bash << 'REMOTE'
CADDYFILE="/etc/caddy/Caddyfile"
if [ ! -f "$CADDYFILE" ]; then
    echo "No Caddyfile found — skipping Caddy config (access via port 8000 directly won't work since it's localhost-only)"
    echo "To expose directly, change MCP_HOST to 0.0.0.0 in /etc/systemd/system/report-needs.service"
    exit 0
fi

echo "Current Caddyfile:"
cat "$CADDYFILE"
echo ""

if grep -q "report-needs\|/mcp" "$CADDYFILE" 2>/dev/null; then
    echo "MCP route already present in Caddyfile — skipping"
else
    echo "Adding /mcp route to Caddyfile..."
    python3 << 'PYEOF'
import sys

with open("/etc/caddy/Caddyfile") as f:
    content = f.read()

mcp_block = """
    # report-needs MCP server
    handle /mcp* {
        reverse_proxy localhost:8000
    }
"""

# Insert before the last } in the file
last_brace = content.rfind("}")
if last_brace == -1:
    print("ERROR: Could not find closing brace in Caddyfile")
    sys.exit(1)

new_content = content[:last_brace] + mcp_block + content[last_brace:]

with open("/etc/caddy/Caddyfile", "w") as f:
    f.write(new_content)

print("Caddyfile updated successfully")
PYEOF

    echo "Validating Caddy config..."
    if caddy validate --config "$CADDYFILE"; then
        caddy reload --config "$CADDYFILE" && echo "Caddy reloaded OK"
    else
        echo "ERROR: Caddy config invalid — reverting"
        # Show what we wrote
        cat "$CADDYFILE"
    fi
fi
REMOTE

echo ""
echo "=== Step 11: Final connectivity check ==="
ssh "$VPS" bash << 'REMOTE'
echo "Service status:"
systemctl is-active report-needs

echo ""
echo "Port listening:"
ss -tlnp | grep 8000

echo ""
echo "MCP endpoint (via localhost):"
curl -s -o /dev/null -w "HTTP %{http_code}" http://127.0.0.1:8000/mcp || echo "not reachable"
echo ""

# Test via Caddy if available
if systemctl is-active caddy &>/dev/null; then
    echo "MCP endpoint (via Caddy):"
    curl -s -o /dev/null -w "HTTP %{http_code}" http://localhost/mcp || echo "not reachable via Caddy"
    echo ""
fi
REMOTE

echo ""
echo "==================================================================="
echo "Deployment complete."
echo ""
echo "MCP server is running on VPS, bound to localhost:8000"
echo "FastMCP serves at path: /mcp"
echo ""
echo "Connect with:"
echo "  http://157.230.82.223/mcp   (via Caddy, if route was added)"
echo ""
echo "For Claude Desktop (claude_desktop_config.json):"
cat << 'CONFIG'
{
  "mcpServers": {
    "report-needs": {
      "url": "http://157.230.82.223/mcp"
    }
  }
}
CONFIG
echo ""
echo "Check logs: ssh $VPS 'journalctl -u report-needs -f'"
echo "==================================================================="
