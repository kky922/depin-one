#!/bin/bash
#==============================================================================
# DePIN ONE — CLI Management Tool
# Usage: depin-one <command>
#==============================================================================
set -euo pipefail

BOT_DIR="$HOME/.depin-one"
VENV="$BOT_DIR/venv/bin/python3"
BOT_SCRIPT="$BOT_DIR/bot/main.py"
PLIST="$HOME/Library/LaunchAgents/io.depin.one.bot.plist"
CONFIG="$BOT_DIR/.env"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

cmd_status() {
    echo -e "${BLUE}DePIN ONE Status${NC}"
    echo "───────────────"
    if launchctl list | grep -q "io.depin.one.bot"; then
        PID=$(launchctl list | grep "io.depin.one.bot" | awk '{print $1}')
        if [[ "$PID" != "-" ]]; then
            echo -e "  Status: ${GREEN}Running${NC} (PID: $PID)"
        else
            echo -e "  Status: ${YELLOW}Stopped${NC}"
        fi
    else
        echo -e "  Status: ${RED}Not installed${NC}"
    fi

    # Check each node
    if [[ -f "$VENV" ]]; then
        echo ""
        echo "  Nodes:"
        "$VENV" -c "
from pathlib import Path
p = Path('$BOT_DIR/bot/data/status.json')
if p.exists():
    import json
    d = json.loads(p.read_text())
    for name, info in d.items():
        emoji = '🟢' if info.get('status') == 'running' else '🔴'
        pts = info.get('points', 0)
        print(f'  {emoji} {name}: {pts} pts')
else:
    print('  (no status data yet)')
" 2>/dev/null || echo "  (bot not started yet)"
    fi

    # Uptime
    if [[ -f "$BOT_DIR/logs/stdout.log" ]]; then
        STARTED=$(stat -f "%Sm" -t "%Y-%m-%d %H:%M" "$BOT_DIR/logs/stdout.log" 2>/dev/null || echo "unknown")
        echo ""
        echo "  Started: $STARTED"
    fi
}

cmd_restart() {
    echo "Restarting DePIN ONE..."
    launchctl unload "$PLIST" 2>/dev/null || true
    sleep 2
    launchctl load "$PLIST" 2>/dev/null || true
    sleep 2
    cmd_status
}

cmd_stop() {
    echo "Stopping DePIN ONE..."
    launchctl unload "$PLIST" 2>/dev/null || true
    echo -e "${YELLOW}Stopped${NC}"
}

cmd_start() {
    echo "Starting DePIN ONE..."
    launchctl load "$PLIST" 2>/dev/null || true
    sleep 2
    cmd_status
}

cmd_logs() {
    if [[ -f "$BOT_DIR/logs/stdout.log" ]]; then
        tail -50 "$BOT_DIR/logs/stdout.log"
    else
        echo "No logs yet"
    fi
}

cmd_update() {
    echo "Updating DePIN ONE..."
    cd "$BOT_DIR"
    source venv/bin/activate
    pip install --quiet --upgrade aiohttp requests playwright apscheduler loguru web3 2>&1 | tail -1
    # Update bot code from GitHub
    if command -v git &>/dev/null && [[ -d "$BOT_DIR/.git" ]]; then
        git pull 2>/dev/null || echo "No git repo, skipping"
    fi
    echo "Update complete. Restart recommended: depin-one restart"
}

cmd_uninstall() {
    echo -e "${RED}This will remove all DePIN ONE files!${NC}"
    read -p "Are you sure? (y/N): " confirm
    if [[ "$confirm" == "y" || "$confirm" == "Y" ]]; then
        launchctl unload "$PLIST" 2>/dev/null || true
        rm -f "$PLIST"
        rm -rf "$BOT_DIR"
        echo "DePIN ONE removed"
    else
        echo "Cancelled"
    fi
}

cmd_edit() {
    if [[ -f "$CONFIG" ]]; then
        if command -v nano &>/dev/null; then
            nano "$CONFIG"
        elif command -v vim &>/dev/null; then
            vim "$CONFIG"
        else
            echo "Open $CONFIG in any editor"
        fi
        cmd_restart
    else
        echo "Config not found"
    fi
}

case "${1:-help}" in
    status|s)    cmd_status ;;
    restart|r)   cmd_restart ;;
    start)       cmd_start ;;
    stop)        cmd_stop ;;
    logs|l)      cmd_logs ;;
    update|u)    cmd_update ;;
    uninstall)   cmd_uninstall ;;
    edit|config) cmd_edit ;;
    help|*)
        echo "DePIN ONE — DePIN Node Manager"
        echo ""
        echo "Usage: depin-one <command>"
        echo ""
        echo "Commands:"
        echo "  status (s)     → Show status"
        echo "  start          → Start bot"
        echo "  stop           → Stop bot"
        echo "  restart (r)    → Restart bot"
        echo "  logs (l)       → Show recent logs"
        echo "  update (u)     → Update dependencies"
        echo "  edit           → Edit config"
        echo "  uninstall      → Remove completely"
        echo "  help           → This message"
        ;;
esac
