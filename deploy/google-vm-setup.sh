#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="$(id -un)"
VENV_DIR="$APP_DIR/.venv"
ENV_FILE="$APP_DIR/.env"
SERVICE_FILE="/etc/systemd/system/buildwise-ai.service"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE"
  echo "Copy .env.example to .env, add the four required values, then run this installer again."
  exit 1
fi

required=(
  TELEGRAM_BOT_TOKEN
  OPENAI_API_KEY
  SUPABASE_URL
  SUPABASE_SECRET_KEY
)

for name in "${required[@]}"; do
  value="$(sed -n "s/^${name}=//p" "$ENV_FILE" | tail -n 1)"
  if [[ -z "$value" || "$value" == replace_* || "$value" == https://your-project.supabase.co ]]; then
    echo "Set a real value for $name in .env before installing."
    exit 1
  fi
done

echo "Installing the small Python runtime BuildWise needs..."
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip curl

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r "$APP_DIR/requirements.txt"
chmod 600 "$ENV_FILE"

tmp_service="$(mktemp)"
cat > "$tmp_service" <<EOF
[Unit]
Description=BuildWise AI Telegram Bot
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV_DIR/bin/python $APP_DIR/main.py
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

sudo install -m 0644 "$tmp_service" "$SERVICE_FILE"
rm -f "$tmp_service"
sudo systemctl daemon-reload
sudo systemctl enable --now buildwise-ai

echo
echo "BuildWise AI has been installed and started."
echo "Status: sudo systemctl status buildwise-ai --no-pager"
echo "Logs:   sudo journalctl -u buildwise-ai -f"
echo "Health: curl http://127.0.0.1:8000/health"
