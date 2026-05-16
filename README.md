# DePIN ONE 🚀

> One command to run 8+ DePIN nodes on your Mac. Auto-setup, auto-restart, Telegram alerts.

## ✨ Features

- **8+ DePIN Nodes** in one install: Teneo, Grass, Dawn, Gradient, Rivalz, Bless, Gata, Ink
- **One Command Setup** — `curl -fsSL https://depin.one/install.sh | bash`
- **Auto-Restart** — launchd keeps nodes alive 24/7, survives reboots
- **Telegram Alerts** — daily earnings reports + crash notifications
- **Interactive Config** — guided setup asks for credentials once
- **Local & Private** — everything runs on your Mac, no cloud dependency

## 🚀 Quick Start

```bash
curl -fsSL https://depin.one/install.sh | bash
```

Then check status:
```bash
depin-one status
```

## 📋 Management

| Command | Description |
|---------|-------------|
| `depin-one status` | Show all node statuses and earnings |
| `depin-one restart` | Restart all nodes |
| `depin-one logs` | View recent logs |
| `depin-one edit` | Edit credentials |
| `depin-one update` | Update dependencies |
| `depin-one uninstall` | Remove everything |

## 🔧 Manual Setup

```bash
git clone https://github.com/kangkuyun/depin-one.git
cd depin-one
cp .env.example .env
# Edit .env with your credentials
./scripts/install.sh
```

## 📁 Structure

```
~/.depin-one/
├── bot/          # DePIN bot (Python)
│   ├── main.py
│   ├── nodes/    # Individual node modules
│   ├── core/     # Scheduler, database
│   └── monitor/  # Telegram alerts
├── launchd/      # Auto-start plist
├── logs/         # Runtime logs
├── .env          # Your credentials
└── venv/         # Python virtual environment
```

## 🤝 Contributing

PRs welcome! Add new node modules or improve the installer.

## ⚠️ Disclaimer

This is a tool for DePIN enthusiasts. Not financial advice. DYOR.

## 📱 Community

- [GitHub Issues](https://github.com/kangkuyun/depin-one/issues)
