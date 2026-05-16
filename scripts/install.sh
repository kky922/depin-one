#!/bin/bash
#==============================================================================
# DePIN ONE — One-Command DePIN Node Installer for macOS
# curl -fsSL https://depin.one/install.sh | bash
#==============================================================================
set -euo pipefail

VERSION="1.0.0"
REPO_URL="https://github.com/kky922/depin-one"
BOT_DIR="$HOME/.depin-one"
LOG_FILE="/tmp/depin-one-install.log"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}"
echo "╔═══════════════════════════════════════════╗"
echo "║       🔷 DePIN ONE  v${VERSION}            ║"
echo "║   One-Command DePIN Node Installer        ║"
echo "╚═══════════════════════════════════════════╝"
echo -e "${NC}"

# --- Referral info ---
echo -e "${YELLOW}🔗 아래 레퍼럴 링크로 가입하시면 더 많은 혜택을 받을 수 있습니다${NC}"
echo ""
echo -e "  ${CYAN}Teneo:${NC}    https://dashboard.teneo.pro/auth/signup?referralCode=eFOKJ"
echo -e "  ${CYAN}Grass:${NC}    https://app.grass.io/register?referralCode=s2UeYu1oB6CHVm3"
echo -e "  ${CYAN}Dawn:${NC}     https://dashboard.dawninternet.com/signup?ref=E4TQJQXT"
echo -e "  ${CYAN}Gradient:${NC} https://app.gradient.network/signup?ref=IXZ171"
echo -e "  ${CYAN}Gata:${NC}     https://app.gata.net?invite_code=g4zp66e8"
echo ""

# --- Helper functions ---
log()   { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; }
info()  { echo -e "${BLUE}[i]${NC} $1"; }

# --- Step 1: Check macOS ---
echo ""
info "Step 1/7: Checking system requirements..."
if [[ "$(uname)" != "Darwin" ]]; then
    error "This installer is for macOS only. Detected: $(uname)"
    exit 1
fi
log "macOS detected: $(sw_vers -productVersion)"

MACOS_VERSION=$(sw_vers -productVersion | cut -d. -f1)
if [[ "$MACOS_VERSION" -lt 12 ]]; then
    warn "macOS 12+ recommended (detected: $(sw_vers -productVersion))"
fi

# --- Step 2: Install Homebrew ---
info "Step 2/7: Ensuring Homebrew..."
if ! command -v brew &>/dev/null; then
    echo "  Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" 2>&1 | tee -a "$LOG_FILE"
    log "Homebrew installed"
else
    log "Homebrew already installed ($(brew --version | head -1))"
fi

# --- Step 3: Install Python ---
info "Step 3/7: Ensuring Python 3..."
PYTHON_CMD=""
for cmd in python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v "$cmd" &>/dev/null; then
        PY_VER=$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+')
        MAJOR=$(echo "$PY_VER" | cut -d. -f1)
        MINOR=$(echo "$PY_VER" | cut -d. -f2)
        if [[ "$MAJOR" -ge 3 && "$MINOR" -ge 9 ]]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [[ -z "$PYTHON_CMD" ]]; then
    echo "  Installing Python 3.12 via Homebrew..."
    brew install python@3.12 2>&1 | tee -a "$LOG_FILE"
    PYTHON_CMD="python3.12"
fi
log "Python: $($PYTHON_CMD --version) ($PYTHON_CMD)"

# --- Step 4: Create virtual environment & install bot ---
info "Step 4/7: Setting up DePIN ONE..."
mkdir -p "$BOT_DIR"
cd "$BOT_DIR"

# Create virtual environment
if [[ ! -d "venv" ]]; then
    "$PYTHON_CMD" -m venv venv
    log "Virtual environment created"
fi
source venv/bin/activate

# Install dependencies
pip install --quiet --upgrade pip 2>&1 | tee -a "$LOG_FILE"
pip install --quiet \
    aiohttp requests websockets \
    playwright playwright-stealth \
    apscheduler python-telegram-bot \
    sqlalchemy aiosqlite pyyaml python-dotenv \
    loguru schedule psutil web3 2>&1 | tee -a "$LOG_FILE"
log "Python dependencies installed"

# Install Playwright browsers
echo "  Installing Playwright browsers (headless Chromium)..."
python -m playwright install chromium 2>&1 | tee -a "$LOG_FILE"
log "Playwright browsers installed"

# --- Step 5: Interactive setup ---
info "Step 5/7: Configuration..."

setup_prompt() {
    local name=$1
    local example=$2
    local value=""
    read -p "  $name [$example]: " value
    echo "${value:-$example}"
}

echo ""
echo "  Enter your credentials for each DePIN node."
echo "  Press Enter to skip any node (can configure later)."
echo ""

# We'll create the config interactively
cat > "$BOT_DIR/.env" << 'ENVEOF'
# DePIN ONE — Auto-generated configuration
# Edit this file to update your credentials
ENVEOF

add_env() {
    echo "$1=$2" >> "$BOT_DIR/.env"
}

# Telegram (optional)
read -p "  Telegram Bot Token (optional, for alerts): " TG_TOKEN
read -p "  Telegram Chat ID (optional): " TG_CHAT
if [[ -n "$TG_TOKEN" ]]; then
    add_env "TELEGRAM_BOT_TOKEN" "$TG_TOKEN"
    add_env "TELEGRAM_CHAT_ID" "$TG_CHAT"
fi

# Common email
read -p "  Common Email (for Grass, Dawn, Gradient, Teneo): " COMMON_EMAIL
read -p "  Common Password (for Grass, Dawn, Gradient, Teneo): " COMMON_PASS

if [[ -n "$COMMON_EMAIL" ]]; then
    add_env "GRASS_EMAIL" "$COMMON_EMAIL"
    add_env "DAWN_EMAIL" "$COMMON_EMAIL"
    add_env "GRADIENT_EMAIL" "$COMMON_EMAIL"
    add_env "TENEO_EMAIL" "$COMMON_EMAIL"
fi
if [[ -n "$COMMON_PASS" ]]; then
    add_env "GRASS_PASSWORD" "$COMMON_PASS"
    add_env "DAWN_PASSWORD" "$COMMON_PASS"
    add_env "GRADIENT_PASSWORD" "$COMMON_PASS"
    add_env "TENEO_PASSWORD" "$COMMON_PASS"
fi

# Wallet address
read -p "  Wallet Address (for Rivalz/Gata, e.g. 0x...): " WALLET
if [[ -n "$WALLET" ]]; then
    add_env "RIVALZ_WALLET_ADDRESS" "$WALLET"
    add_env "GATA_WALLET_ADDRESS" "$WALLET"
fi

log "Configuration saved to $BOT_DIR/.env"

# --- Step 6: Install launchd service ---
info "Step 6/7: Installing auto-start service..."

cat > "$BOT_DIR/launchd/io.depin.one.bot.plist" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>io.depin.one.bot</string>
    <key>ProgramArguments</key>
    <array>
        <string>$BOT_DIR/venv/bin/python3</string>
        <string>$BOT_DIR/bot/main.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$BOT_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$BOT_DIR/logs/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$BOT_DIR/logs/stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
PLISTEOF

cp "$BOT_DIR/launchd/io.depin.one.bot.plist" ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/io.depin.one.bot.plist 2>/dev/null || true
log "Auto-start service installed (launchd)"

# --- Step 7: Final checks ---
info "Step 7/7: Finalizing..."
sleep 3
if launchctl list | grep -q "io.depin.one.bot"; then
    log "DePIN ONE is running!"
else
    warn "Service may need a moment. Check: launchctl list | grep depin"
fi

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║       ✅ DePIN ONE  설치 완료!            ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════╝${NC}"
echo ""
echo "  📁 설치 위치: $BOT_DIR"
echo "  ⚙️  설정 파일: $BOT_DIR/.env"
echo "  📊 로그: $BOT_DIR/logs/"
echo ""
echo "  명령어:"
echo "    depin-one status     → 현재 상태 확인"
echo "    depin-one restart    → 재시작"
echo "    depin-one update     → 업데이트"
echo "    depin-one uninstall  → 제거"
echo ""
echo -e "${YELLOW}  📱 Telegram 알림을 설정하려면:${NC}"
echo "    @BotFather 에서 봇 생성 → 토큰을 .env에 입력"
echo ""
echo "  수익 많이 내세요! 🚀"
