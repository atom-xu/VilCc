#!/bin/bash
# VilCC uninstaller

set -e

echo "=== VilCC Uninstaller ==="
echo ""

# ── 1. Stop running processes ──────────────────────────────────────────────
echo "Stopping VilCC processes..."
pkill -f "uvicorn main:app" 2>/dev/null && echo "  ✓ REST server stopped" || echo "  · REST server not running"
pkill -f "python mcp_server.py" 2>/dev/null && echo "  ✓ MCP server stopped" || echo "  · MCP server not running"

# ── 2. Remove MCP config from AI clients ──────────────────────────────────
echo ""
echo "Removing MCP config from AI clients..."

remove_mcp_entry() {
    local label="$1"
    local file="$2"
    local key="${3:-vilcc}"

    file="${file/#\~/$HOME}"
    if [ ! -f "$file" ]; then
        return
    fi

    if python3 - "$file" "$key" <<'PYEOF'
import sys, json, pathlib
path, key = pathlib.Path(sys.argv[1]), sys.argv[2]
try:
    data = json.loads(path.read_text())
except Exception:
    sys.exit(0)

removed = False
for section in ("mcpServers",):
    if section in data and key in data[section]:
        del data[section][key]
        removed = True

# OpenClaw nested structure
if "mcp" in data and "servers" in data["mcp"] and key in data["mcp"]["servers"]:
    del data["mcp"]["servers"][key]
    removed = True

if removed:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"  ✓ Removed from {path}")
else:
    print(f"  · Not found in {path}")
PYEOF
    then
        :
    fi
}

remove_mcp_entry "Claude Desktop"  "~/Library/Application Support/Claude/claude_desktop_config.json"
remove_mcp_entry "Claude Code"     "~/.claude.json"
remove_mcp_entry "Cursor"          "~/.cursor/mcp.json"
remove_mcp_entry "VS Code"         "~/Library/Application Support/Code/User/settings.json"
remove_mcp_entry "Windsurf"        "~/.codeium/windsurf/mcp_config.json"
remove_mcp_entry "Trae/MarsCode"   "~/Library/Application Support/Trae/User/mcp.json"
remove_mcp_entry "OpenClaw"        "~/.openclaw/openclaw.json"

# ── 3. Delete project folder ───────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo ""
echo "Project folder: $SCRIPT_DIR"
read -r -p "Delete the project folder? [y/N] " confirm
if [[ "$confirm" =~ ^[Yy]$ ]]; then
    rm -rf "$SCRIPT_DIR"
    echo "  ✓ Project folder deleted"
else
    echo "  · Skipped"
fi

echo ""
echo "Done. Remember to restart any AI clients you use."
