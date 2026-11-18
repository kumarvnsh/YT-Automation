#!/usr/bin/env bash
set -euo pipefail
# Run as root inside a new Ubuntu-24.04 WSL distribution, after Windows restart.
[[ $(id -u) == 0 ]] || { echo 'Run this installer as root inside WSL.'; exit 1; }
: "${SUPERBOT_AUTH_PASSWORD:?Installer needs a generated local bridge password}"
BOT_USER=histold-bots
BOT_HOME=/home/$BOT_USER
PIN=652e100fc3412239fd5a07ca7bb8566213c5b6fa
REPO=/mnt/d/YT-Automation
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq build-essential pkg-config libssl-dev git curl ca-certificates xz-utils python3
id "$BOT_USER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$BOT_USER"
install -d -o "$BOT_USER" -g "$BOT_USER" "$BOT_HOME/.local" "$BOT_HOME/.config/superbot" "$BOT_HOME/superbot-data" "$BOT_HOME/superbot-workspace"

# Official Node distribution, verified against the vendor's checksum manifest.
if [[ ! -x /opt/histold-node/bin/node ]]; then
  NODE_TMP=$(mktemp -d /tmp/histold-node.XXXXXX)
  cd "$NODE_TMP"
  curl -fsSLo SHASUMS256.txt https://nodejs.org/dist/latest-v24.x/SHASUMS256.txt
  NODE_ARCHIVE=$(awk '$2 ~ /linux-x64.tar.xz$/ {print $2; exit}' SHASUMS256.txt)
  [[ "$NODE_ARCHIVE" =~ ^node-v[0-9.]+-linux-x64.tar.xz$ ]] || exit 1
  curl -fsSLO "https://nodejs.org/dist/latest-v24.x/$NODE_ARCHIVE"
  grep "  $NODE_ARCHIVE$" SHASUMS256.txt | sha256sum -c -
  install -d /opt/histold-node
  tar -xJf "$NODE_ARCHIVE" --strip-components=1 -C /opt/histold-node
fi
cd "$BOT_HOME"
if [[ ! -e /usr/local/bin/node ]]; then ln -s /opt/histold-node/bin/node /usr/local/bin/node; fi
export PATH="/opt/histold-node/bin:$BOT_HOME/.local/bin:$PATH"
runuser -u "$BOT_USER" -- env PATH="$PATH" npm install --global --prefix "$BOT_HOME/.local" @openai/codex
if [[ ! -x "$BOT_HOME/.cargo/bin/cargo" ]]; then
  runuser -u "$BOT_USER" -- bash -c 'curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal'
fi
if [[ ! -d "$BOT_HOME/superbot/.git" ]]; then
  runuser -u "$BOT_USER" -- git init "$BOT_HOME/superbot"
  runuser -u "$BOT_USER" -- git -C "$BOT_HOME/superbot" remote add origin https://github.com/davidondrej/superbot.git
  runuser -u "$BOT_USER" -- git -C "$BOT_HOME/superbot" fetch --depth 1 origin "$PIN"
  runuser -u "$BOT_USER" -- git -C "$BOT_HOME/superbot" checkout --detach FETCH_HEAD
fi
printf '%s' '__PATCH_B64__' | base64 -d > /tmp/histold-superbot-read-only.patch
printf '%s' '__STATUS_PATCH_B64__' | base64 -d > /tmp/histold-superbot-message-status.patch
for PATCH in /tmp/histold-superbot-read-only.patch /tmp/histold-superbot-message-status.patch; do
  if runuser -u "$BOT_USER" -- git -C "$BOT_HOME/superbot" apply --reverse --check "$PATCH" >/dev/null 2>&1; then
    echo 'Reviewed integration patch is already present.'
  else
    runuser -u "$BOT_USER" -- git -C "$BOT_HOME/superbot" apply --check "$PATCH"
    runuser -u "$BOT_USER" -- git -C "$BOT_HOME/superbot" apply "$PATCH"
  fi
done
[[ $(git -c safe.directory="$BOT_HOME/superbot" -C "$BOT_HOME/superbot" rev-parse HEAD) == "$PIN" ]] || { echo 'Unexpected Superbot revision; inspect before upgrading.'; exit 1; }
runuser -u "$BOT_USER" -- env PATH="$BOT_HOME/.cargo/bin:$PATH" bash -c "cd '$BOT_HOME/superbot' && cargo test --locked && cargo build --release --locked"
export BOT_HOME BOT_USER
python3 - <<'PYCONFIG'
import os,pathlib,pwd,shlex
home=pathlib.Path(os.environ['BOT_HOME'])
path=home/'.config/superbot/env'
values={'AUTH_PASSWORD':os.environ['SUPERBOT_AUTH_PASSWORD'],'HOST':'127.0.0.1','PORT':'3007','DEFAULT_HARNESS':'codex','SCREENS':'off','TZ':'Asia/Kolkata','RUN_TIMEOUT_MINUTES':'15','PRIMARY_BOT_NAME':'Dashboard coordinator','DATA_DIR':str(home/'superbot-data'),'WORKSPACE_DIR':str(home/'superbot-workspace')}
path.write_text(''.join(key+'='+shlex.quote(value)+'\n' for key,value in values.items()))
user=pwd.getpwnam(os.environ['BOT_USER']);os.chown(path,user.pw_uid,user.pw_gid);path.chmod(0o600)
PYCONFIG
cat > "$BOT_HOME/start-superbot.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:/opt/histold-node/bin:$PATH"
set -a
source "$HOME/.config/superbot/env"
set +a
exec "$HOME/superbot/target/release/botd"
SH
chown "$BOT_USER:$BOT_USER" "$BOT_HOME/start-superbot.sh"
chmod 700 "$BOT_HOME/start-superbot.sh"
# Apply on the next WSL distribution restart. Windows publisher files stay out of
# the Linux bots' filesystem; the bridge sends bounded evidence snapshots instead.
cat > /etc/wsl.conf <<'CONF'
[user]
default=histold-bots
[automount]
enabled=false
[interop]
enabled=false
appendWindowsPath=false
CONF
echo 'Superbot built with the read-only Codex adapter. Linux Codex login is the remaining interactive step.'
