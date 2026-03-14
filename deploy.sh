#!/usr/bin/env bash
# deploy.sh — deploy report-needs MCP server to VPS
# Run from: /Users/dembe/Documents/AI/report-needs/
# Prerequisites: ssh-add ~/.ssh/id_ed25519 (unlock key first)

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

# Use python3 — Ubuntu 24.04 ships 3.12
python3 --version

# Create venv if not present
if [ ! -d venv ]; then
    python3 -m venv venv
    echo "venv created"
else
    echo "venv already exists"
fi

# Install/upgrade mcp
venv/bin/pip install --quiet --upgrade pip
venv/bin/pip install --quiet -r requirements.txt
echo "Dependencies installed:"
venv/bin/pip show mcp | grep -E "^(Name|Version):"
REMOTE

echo ""
echo "=== Step 7: Quick smoke test (stdio) ==="
ssh "$VPS" "cd /opt/report-needs && echo '{}' | timeout 3 venv/bin/python server.py stdio 2>&1 | head -5 || true"

echo ""
echo "=== Step 8: Install systemd service ==="
scp "$LOCAL_DIR/report-needs.service" "$VPS:/etc/systemd/system/report-needs.service"
ssh "$VPS" bash << 'REMOTE'
systemctl daemon-reload
systemctl enable report-needs
systemctl restart report-needs
sleep 2
systemctl status report-needs --no-pager
REMOTE

echo ""
echo "=== Step 9: Verify port 8000 is listening ==="
ssh "$VPS" 'ss -tlnp | grep 8000 || echo "WARNING: port 8000 not yet listening"'

echo ""
echo "=== Step 10: Check Caddy and add route if needed ==="
ssh "$VPS" bash << 'REMOTE'
CADDYFILE="/etc/caddy/Caddyfile"
if [ ! -f "$CADDYFILE" ]; then
    echo "No Caddyfile found — skipping Caddy config"
    exit 0
fi

echo "Current Caddyfile:"
cat "$CADDYFILE"

# Check if mcp route already present
if grep -q "report-needs\|/mcp" "$CADDYFILE" 2>/dev/null; then
    echo ""
    echo "MCP route already present in Caddyfile — skipping"
else
    echo ""
    echo "Adding /mcp route to Caddyfile..."
    # Append a reverse_proxy block for /mcp to the existing server block
    # We insert before the last closing brace of the first server block
    python3 << 'PYEOF'
import re, sys

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
    caddy validate --config "$CADDYFILE" && caddy reload --config "$CADDYFILE" && echo "Caddy reloaded OK"
fi
REMOTE

echo ""
echo "=== Deployment complete ==="
echo ""
echo "MCP server endpoint options:"
echo "  Direct:  http://157.230.82.223:8000/mcp"
echo "  Via Caddy: http://157.230.82.223/mcp (if Caddy configured)"
echo ""
echo "To add to Claude Desktop (claude_desktop_config.json):"
cat << 'CONFIG'
{
  "mcpServers": {
    "report-needs": {
      "url": "http://157.230.82.223:8000/mcp"
    }
  }
}
CONFIG
echo ""
echo "Check logs: ssh $VPS 'journalctl -u report-needs -n 50'"
