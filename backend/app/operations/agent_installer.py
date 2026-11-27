"""Agent packaging, one-click installer script generation, OS env persistence helpers, and local start."""
from __future__ import annotations

from dataclasses import dataclass
import io
import os
from pathlib import Path
import platform
import shlex
import socket
import subprocess
import sys
import tarfile
from typing import Any
from urllib.parse import urlparse
import zipfile

ROOT_DIR = Path(__file__).resolve().parents[3]
EXECUTOR_DIR = ROOT_DIR / "executor"

SUPPORTED_AGENT_OS: dict[str, dict[str, str]] = {
    "macos_arm64": {
        "label": "macOS (Apple Silicon / M系列)",
        "archive_ext": ".tar.gz",
        "script_ext": ".sh",
        "shell": "bash",
        "keepalive": "launchd KeepAlive=true (5秒崩溃自启) + caffeinate 防休眠 + 120s看门狗 + 单实例锁",
    },
    "macos_x64": {
        "label": "macOS (Intel x64)",
        "archive_ext": ".tar.gz",
        "script_ext": ".sh",
        "shell": "bash",
        "keepalive": "launchd KeepAlive=true (5秒崩溃自启) + caffeinate 防休眠 + 120s看门狗 + 单实例锁",
    },
    "windows_x64": {
        "label": "Windows 10/11 (x64)",
        "archive_ext": ".zip",
        "script_ext": ".ps1",
        "shell": "powershell",
        "keepalive": "登录计划任务 (失败1分钟重启) + 3秒外层循环自拉起 + 120s看门狗 + 单实例锁",
    },
    "linux_x64": {
        "label": "Linux (x64)",
        "archive_ext": ".tar.gz",
        "script_ext": ".sh",
        "shell": "bash",
        "keepalive": "systemd user Restart=always (5秒自启) + nohup循环兜底 + 120s看门狗 + 单实例锁",
    },
}

DEFAULT_PROTOCOL_SCHEME = "acq-vcat-agent"


def default_agent_download_urls(base_url: str) -> dict[str, str]:
    clean_base = base_url.rstrip("/")
    return {
        os_key: f"{clean_base}/api/ops/agent/package?os={os_key}"
        for os_key in SUPPORTED_AGENT_OS
    }


def validate_download_urls(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("各操作系统 Agent 下载地址配置必须是字典")
    cleaned: dict[str, str] = {}
    for key, val in raw.items():
        if key not in SUPPORTED_AGENT_OS:
            raise ValueError(f"不支持的操作系统类型: {key}")
        if val is None:
            cleaned[key] = ""
            continue
        if not isinstance(val, str):
            raise ValueError(f"下载地址必须是文本: {key}")
        url = val.strip()
        if not url:
            cleaned[key] = ""
            continue
        if len(url) > 1000:
            raise ValueError(f"下载地址长度超过限制: {key}")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"下载地址必须为有效 HTTP/HTTPS URL: {key}")
        cleaned[key] = url
    return cleaned


def resolve_agent_download_urls(base_url: str, custom_urls: dict[str, Any] | None) -> dict[str, str]:
    defaults = default_agent_download_urls(base_url)
    custom = custom_urls or {}
    resolved: dict[str, str] = {}
    for os_key, default_url in defaults.items():
        val = custom.get(os_key)
        resolved[os_key] = val.strip() if isinstance(val, str) and val.strip() else default_url
    return resolved


def _render_posix_start_sh(is_macos: bool) -> str:
    caffeinate_block = (
        'if command -v caffeinate >/dev/null 2>&1; then\n'
        '  exec caffeinate -is "$PY_BIN" "$APP_DIR/vcat_agent.py" --env-file "$ENV_FILE"\n'
        'fi\n'
        if is_macos
        else ""
    )
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'BASE_DIR="${VCAT_STATE_DIR:-$HOME/.acquisition-vcat-agent}"\n'
        'APP_DIR="${ACQ_VCAT_AGENT_HOME:-$BASE_DIR/app}"\n'
        'ENV_FILE="$BASE_DIR/vcat_agent.env"\n'
        'if [ -f "$ENV_FILE" ]; then\n'
        "  set -a\n"
        '  # shellcheck disable=SC1090\n'
        '  source "$ENV_FILE"\n'
        "  set +a\n"
        "fi\n"
        'export VCAT_STATE_DIR="$BASE_DIR"\n'
        'export ACQ_VCAT_AGENT_HOME="$APP_DIR"\n'
        'export ACQ_VCAT_AGENT_BIN="$BASE_DIR/start-agent.sh"\n'
        'PY_BIN="${ACQ_VCAT_AGENT_PYTHON:-$BASE_DIR/venv/bin/python}"\n'
        'if [ ! -x "$PY_BIN" ]; then\n'
        '  PY_BIN="$(command -v python3)"\n'
        "fi\n"
        'export ACQ_VCAT_AGENT_PYTHON="$PY_BIN"\n'
        + caffeinate_block
        + 'exec "$PY_BIN" "$APP_DIR/vcat_agent.py" --env-file "$ENV_FILE"\n'
    )


def _render_macos_install_daemon_sh() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail
BASE_DIR="${VCAT_STATE_DIR:-$HOME/.acquisition-vcat-agent}"
APP_DIR="${ACQ_VCAT_AGENT_HOME:-$BASE_DIR/app}"
START_BIN="$BASE_DIR/start-agent.sh"
PY_BIN="${ACQ_VCAT_AGENT_PYTHON:-$BASE_DIR/venv/bin/python}"
ENV_FILE="$BASE_DIR/vcat_agent.env"
LOG_DIR="$BASE_DIR/logs"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/com.acquisition.vcat-agent.plist"
mkdir -p "$LOG_DIR" "$PLIST_DIR"
chmod +x "$START_BIN"

SERVER_VAL="${VCAT_SERVER_URL:-}"
if [ -z "$SERVER_VAL" ] && [ -f "$ENV_FILE" ]; then
  SERVER_VAL="$(grep -E '^VCAT_SERVER_URL=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
fi

if [ "${ACQ_SKIP_USER_ENV:-0}" != "1" ]; then
  ZSHRC="$HOME/.zshrc"
  touch "$ZSHRC"
  python3 - "$ZSHRC" "$APP_DIR" "$START_BIN" "$PY_BIN" "$SERVER_VAL" <<'PYEOF'
import pathlib, re, sys
rc = pathlib.Path(sys.argv[1])
app_dir, start_bin, py_bin, srv = sys.argv[2:6]
text = rc.read_text(encoding="utf-8") if rc.exists() else ""
block = "\\n".join([
    "# >>> acquisition-vcat-agent env >>>",
    f'export ACQ_VCAT_AGENT_HOME="{app_dir}"',
    f'export ACQ_VCAT_AGENT_BIN="{start_bin}"',
    f'export ACQ_VCAT_AGENT_PYTHON="{py_bin}"',
    f'export VCAT_SERVER_URL="{srv}"',
    "# <<< acquisition-vcat-agent env <<<",
])
pattern = re.compile(r"# >>> acquisition-vcat-agent env >>>.*?# <<< acquisition-vcat-agent env <<<\\n?", re.S)
text = pattern.sub("", text).rstrip()
text = (text + "\\n\\n" + block + "\\n") if text else (block + "\\n")
rc.write_text(text, encoding="utf-8")
PYEOF
  launchctl setenv ACQ_VCAT_AGENT_HOME "$APP_DIR" >/dev/null 2>&1 || true
  launchctl setenv ACQ_VCAT_AGENT_BIN "$START_BIN" >/dev/null 2>&1 || true
  launchctl setenv ACQ_VCAT_AGENT_PYTHON "$PY_BIN" >/dev/null 2>&1 || true
  [ -n "$SERVER_VAL" ] && launchctl setenv VCAT_SERVER_URL "$SERVER_VAL" >/dev/null 2>&1 || true
fi

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.acquisition.vcat-agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$START_BIN</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>ACQ_VCAT_AGENT_HOME</key>
    <string>$APP_DIR</string>
    <key>ACQ_VCAT_AGENT_BIN</key>
    <string>$START_BIN</string>
    <key>ACQ_VCAT_AGENT_PYTHON</key>
    <string>$PY_BIN</string>
    <key>VCAT_STATE_DIR</key>
    <string>$BASE_DIR</string>
    <key>VCAT_SERVER_URL</key>
    <string>$SERVER_VAL</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>5</integer>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/launchd.stdout.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/launchd.stderr.log</string>
</dict>
</plist>
EOF

UID_VAL="$(id -u)"
launchctl bootout "gui/$UID_VAL/com.acquisition.vcat-agent" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID_VAL" "$PLIST_PATH"
launchctl kickstart -k "gui/$UID_VAL/com.acquisition.vcat-agent" >/dev/null 2>&1 || true
echo "macOS LaunchAgent 已安装并启动 (KeepAlive=true): $PLIST_PATH"
"""


def _render_linux_install_daemon_sh() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail
BASE_DIR="${VCAT_STATE_DIR:-$HOME/.acquisition-vcat-agent}"
APP_DIR="${ACQ_VCAT_AGENT_HOME:-$BASE_DIR/app}"
START_BIN="$BASE_DIR/start-agent.sh"
PY_BIN="${ACQ_VCAT_AGENT_PYTHON:-$BASE_DIR/venv/bin/python}"
ENV_FILE="$BASE_DIR/vcat_agent.env"
LOG_DIR="$BASE_DIR/logs"
mkdir -p "$LOG_DIR"
chmod +x "$START_BIN"

SERVER_VAL="${VCAT_SERVER_URL:-}"
if [ -z "$SERVER_VAL" ] && [ -f "$ENV_FILE" ]; then
  SERVER_VAL="$(grep -E '^VCAT_SERVER_URL=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
fi

if [ "${ACQ_SKIP_USER_ENV:-0}" != "1" ]; then
  BASHRC="$HOME/.bashrc"
  touch "$BASHRC"
  python3 - "$BASHRC" "$APP_DIR" "$START_BIN" "$PY_BIN" "$SERVER_VAL" <<'PYEOF'
import pathlib, re, sys
rc = pathlib.Path(sys.argv[1])
app_dir, start_bin, py_bin, srv = sys.argv[2:6]
text = rc.read_text(encoding="utf-8") if rc.exists() else ""
block = "\\n".join([
    "# >>> acquisition-vcat-agent env >>>",
    f'export ACQ_VCAT_AGENT_HOME="{app_dir}"',
    f'export ACQ_VCAT_AGENT_BIN="{start_bin}"',
    f'export ACQ_VCAT_AGENT_PYTHON="{py_bin}"',
    f'export VCAT_SERVER_URL="{srv}"',
    "# <<< acquisition-vcat-agent env <<<",
])
pattern = re.compile(r"# >>> acquisition-vcat-agent env >>>.*?# <<< acquisition-vcat-agent env <<<\\n?", re.S)
text = pattern.sub("", text).rstrip()
text = (text + "\\n\\n" + block + "\\n") if text else (block + "\\n")
rc.write_text(text, encoding="utf-8")
PYEOF
fi

if command -v systemctl >/dev/null 2>&1 && systemctl --user status >/dev/null 2>&1; then
  UNIT_DIR="$HOME/.config/systemd/user"
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT_DIR/acquisition-vcat-agent.service" <<EOF
[Unit]
Description=Acquisition Space Xiaovmao Execution Agent
After=network-online.target

[Service]
Type=simple
EnvironmentFile=$ENV_FILE
Environment=ACQ_VCAT_AGENT_HOME=$APP_DIR
Environment=ACQ_VCAT_AGENT_BIN=$START_BIN
Environment=ACQ_VCAT_AGENT_PYTHON=$PY_BIN
ExecStart=/bin/bash $START_BIN
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now acquisition-vcat-agent.service
  echo "Linux systemd --user 守护服务已安装并启动"
else
  nohup bash -c 'while true; do "'"$START_BIN"'" >> "'"$LOG_DIR"'/daemon.log" 2>&1; sleep 3; done' >/dev/null 2>&1 &
  echo "已以后台 Watchdog 循环拉起 Agent（日志文件: $LOG_DIR/daemon.log）"
fi
"""


def _render_windows_start_cmd() -> str:
    return """@echo off
setlocal enabledelayedexpansion
if "%VCAT_STATE_DIR%"=="" set "VCAT_STATE_DIR=%USERPROFILE%\\.acquisition-vcat-agent"
if "%ACQ_VCAT_AGENT_HOME%"=="" set "ACQ_VCAT_AGENT_HOME=%VCAT_STATE_DIR%\\app"
set "ACQ_VCAT_AGENT_BIN=%VCAT_STATE_DIR%\\start-agent.cmd"
set "ENV_FILE=%VCAT_STATE_DIR%\\vcat_agent.env"
set "PY_BIN=%ACQ_VCAT_AGENT_PYTHON%"
if "%PY_BIN%"=="" set "PY_BIN=%VCAT_STATE_DIR%\\venv\\Scripts\\python.exe"
if not exist "%PY_BIN%" set "PY_BIN=python"
if not exist "%VCAT_STATE_DIR%\\logs" mkdir "%VCAT_STATE_DIR%\\logs"

:loop
"%PY_BIN%" "%ACQ_VCAT_AGENT_HOME%\\vcat_agent.py" --env-file "%ENV_FILE%" >> "%VCAT_STATE_DIR%\\logs\\console.log" 2>&1
timeout /t 3 /nobreak >nul
goto loop
"""


def _render_windows_install_daemon_ps1() -> str:
    return """$ErrorActionPreference = "Stop"
$BaseDir = if ($env:VCAT_STATE_DIR) { $env:VCAT_STATE_DIR } else { Join-Path $env:USERPROFILE ".acquisition-vcat-agent" }
$AppDir = if ($env:ACQ_VCAT_AGENT_HOME) { $env:ACQ_VCAT_AGENT_HOME } else { Join-Path $BaseDir "app" }
$StartBin = Join-Path $BaseDir "start-agent.cmd"
$PyBin = if ($env:ACQ_VCAT_AGENT_PYTHON) { $env:ACQ_VCAT_AGENT_PYTHON } else { Join-Path $BaseDir "venv\\Scripts\\python.exe" }
$EnvFile = Join-Path $BaseDir "vcat_agent.env"

$ServerUrl = $env:VCAT_SERVER_URL
if (-not $ServerUrl -and (Test-Path $EnvFile)) {
  foreach ($line in Get-Content $EnvFile) {
    if ($line -match "^VCAT_SERVER_URL=(.+)$") { $ServerUrl = $Matches[1].Trim() }
  }
}

if ($env:ACQ_SKIP_USER_ENV -ne "1") {
  [Environment]::SetEnvironmentVariable("ACQ_VCAT_AGENT_HOME", $AppDir, "User")
  [Environment]::SetEnvironmentVariable("ACQ_VCAT_AGENT_BIN", $StartBin, "User")
  [Environment]::SetEnvironmentVariable("ACQ_VCAT_AGENT_PYTHON", $PyBin, "User")
  if ($ServerUrl) {
    [Environment]::SetEnvironmentVariable("VCAT_SERVER_URL", $ServerUrl, "User")
  }
  $ProtoKey = "HKCU:\\Software\\Classes\\acq-vcat-agent"
  New-Item -Path "$ProtoKey\\shell\\open\\command" -Force | Out-Null
  Set-ItemProperty -Path $ProtoKey -Name "(Default)" -Value "URL:Acquisition Vcat Agent"
  Set-ItemProperty -Path $ProtoKey -Name "URL Protocol" -Value ""
  Set-ItemProperty -Path "$ProtoKey\\shell\\open\\command" -Name "(Default)" -Value "`"$StartBin`""
}

$TaskName = "AcquisitionVcatAgent"
$Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$StartBin`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 0) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Windows 计划任务守护已注册并启动 ($TaskName)，已写入环境变量 ACQ_VCAT_AGENT_BIN=$StartBin"
"""


def _read_executor_asset(filename: str) -> bytes:
    path = EXECUTOR_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"缺少执行器资产文件: {filename}")
    return path.read_bytes()


def build_agent_package(os_key: str) -> tuple[str, str, bytes]:
    """Build an archive (.tar.gz or .zip) containing ONLY safe public executor sources + daemon scripts."""
    if os_key not in SUPPORTED_AGENT_OS:
        raise ValueError(f"不支持的操作系统类型: {os_key}")

    files: dict[str, tuple[bytes, int]] = {
        "vcat_agent.py": (_read_executor_asset("vcat_agent.py"), 0o755),
        "requirements.txt": (_read_executor_asset("requirements.txt"), 0o644),
        "vcat_agent.env.example": (_read_executor_asset("vcat_agent.env.example"), 0o644),
    }
    if os_key in ("macos_arm64", "macos_x64"):
        files["start-agent.sh"] = (_render_posix_start_sh(is_macos=True).encode("utf-8"), 0o755)
        files["install-daemon.sh"] = (_render_macos_install_daemon_sh().encode("utf-8"), 0o755)
    elif os_key == "linux_x64":
        files["start-agent.sh"] = (_render_posix_start_sh(is_macos=False).encode("utf-8"), 0o755)
        files["install-daemon.sh"] = (_render_linux_install_daemon_sh().encode("utf-8"), 0o755)
    else:
        files["start-agent.cmd"] = (_render_windows_start_cmd().encode("utf-8"), 0o755)
        files["install-daemon.ps1"] = (_render_windows_install_daemon_ps1().encode("utf-8"), 0o644)

    buf = io.BytesIO()
    if os_key == "windows_x64":
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name, (content, _) in files.items():
                zf.writestr(name, content)
        return f"acquisition-vcat-agent-{os_key}.zip", "application/zip", buf.getvalue()

    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, (content, mode) in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            info.mode = mode
            tf.addfile(info, io.BytesIO(content))
    return f"acquisition-vcat-agent-{os_key}.tar.gz", "application/gzip", buf.getvalue()


def render_install_script(
    *,
    os_key: str,
    download_url: str,
    server_url: str,
    agent_mode: str,
    pair_code: str,
    node_name: str,
    poll_wait: float = 25.0,
) -> tuple[str, str, str]:
    """Return (script_filename, script_content, one_line_command) for one-click install + autostart."""
    if os_key not in SUPPORTED_AGENT_OS:
        raise ValueError(f"不支持的操作系统类型: {os_key}")

    safe_name = node_name.strip() or f"{agent_mode}-node"
    clean_server = server_url.rstrip("/")

    if os_key == "windows_x64":
        q_url = download_url.replace("'", "''")
        q_srv = clean_server.replace("'", "''")
        q_pair = pair_code.replace("'", "''")
        q_name = safe_name.replace("'", "''")
        script = f"""$ErrorActionPreference = "Stop"
$BaseDir = Join-Path $env:USERPROFILE ".acquisition-vcat-agent"
$AppDir = Join-Path $BaseDir "app"
$VenvDir = Join-Path $BaseDir "venv"
$EnvFile = Join-Path $BaseDir "vcat_agent.env"
New-Item -ItemType Directory -Force -Path $BaseDir, $AppDir | Out-Null

$DownloadUrl = '{q_url}'
$ServerUrl = '{q_srv}'
$PairCode = '{q_pair}'
$NodeName = '{q_name}'
$AgentMode = '{agent_mode}'
$PollWait = '{int(poll_wait)}'

$TmpPkg = Join-Path $BaseDir "agent-package.download"
Write-Host "[1/5] 正在下载 Agent 安装包: $DownloadUrl"
Invoke-WebRequest -Uri $DownloadUrl -OutFile $TmpPkg
if ($DownloadUrl -match "\\.tar\\.gz|\\.tgz" -or (Get-Command tar -ErrorAction SilentlyContinue -and $DownloadUrl -notmatch "\\.zip")) {{
  try {{
    Expand-Archive -Path $TmpPkg -DestinationPath $AppDir -Force
  }} catch {{
    tar -xzf $TmpPkg -C $AppDir
  }}
}} else {{
  Expand-Archive -Path $TmpPkg -DestinationPath $AppDir -Force
}}
Remove-Item -Force $TmpPkg -ErrorAction SilentlyContinue

Write-Host "[2/5] 创建 Python 虚拟环境并安装依赖..."
if (-not (Test-Path (Join-Path $VenvDir "Scripts\\python.exe"))) {{
  python -m venv $VenvDir
}}
$PyBin = Join-Path $VenvDir "Scripts\\python.exe"
& $PyBin -m pip install --quiet --upgrade pip
& $PyBin -m pip install --quiet -r (Join-Path $AppDir "requirements.txt")

Write-Host "[3/5] 写入环境变量配置 (vcat_agent.env)..."
$StartBin = Join-Path $BaseDir "start-agent.cmd"
if (Test-Path (Join-Path $AppDir "start-agent.cmd")) {{
  Copy-Item -Force (Join-Path $AppDir "start-agent.cmd") $StartBin
}}
$ExistingToken = ""
if (Test-Path $EnvFile) {{
  foreach ($line in Get-Content $EnvFile) {{
    if ($line -match "^VCAT_MCP_TOKEN=(.+)$") {{ $ExistingToken = $Matches[1].Trim() }}
  }}
}}
if (-not $ExistingToken) {{
  $ExistingToken = Read-Host "请输入本机小V猫 MCP 令牌 (VCAT_MCP_TOKEN，可在小V猫客户端开启会员态 MCP 后复制)"
}}
@"
VCAT_SERVER_URL=$ServerUrl
VCAT_AGENT_MODE=$AgentMode
VCAT_MCP_URL=http://127.0.0.1:3199/mcp
VCAT_MCP_TOKEN=$ExistingToken
VCAT_NODE_NAME=$NodeName
VCAT_POLL_WAIT=$PollWait
VCAT_STATE_DIR=$BaseDir
ACQ_VCAT_AGENT_HOME=$AppDir
ACQ_VCAT_AGENT_BIN=$StartBin
ACQ_VCAT_AGENT_PYTHON=$PyBin
"@ | Set-Content -Path $EnvFile -Encoding UTF8

$env:ACQ_VCAT_AGENT_HOME = $AppDir
$env:ACQ_VCAT_AGENT_BIN = $StartBin
$env:ACQ_VCAT_AGENT_PYTHON = $PyBin
$env:VCAT_SERVER_URL = $ServerUrl

Write-Host "[4/5] 完成一次性配对注册..."
& $PyBin (Join-Path $AppDir "vcat_agent.py") --env-file $EnvFile --mode $AgentMode --server $ServerUrl --pair-token $PairCode --node-name $NodeName --register-only

Write-Host "[5/5] 注册 Windows 常驻守护服务并自动启动..."
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $AppDir "install-daemon.ps1")
"""
        one_line = (
            "powershell -NoProfile -ExecutionPolicy Bypass -Command "
            "\"$d=Join-Path $env:USERPROFILE '.acquisition-vcat-agent'; $a=Join-Path $d 'app'; "
            "New-Item -ItemType Directory -Force -Path $d,$a|Out-Null; "
            f"Invoke-WebRequest -Uri '{q_url}' -OutFile (Join-Path $d 'pkg.zip'); "
            "Expand-Archive -Force (Join-Path $d 'pkg.zip') $a; "
            "python -m venv (Join-Path $d 'venv'); "
            "& (Join-Path $d 'venv\\Scripts\\python.exe') -m pip install -q -r (Join-Path $a 'requirements.txt'); "
            "Copy-Item -Force (Join-Path $a 'start-agent.cmd') (Join-Path $d 'start-agent.cmd'); "
            "$tk=Read-Host 'VCAT_MCP_TOKEN'; "
            f"@('VCAT_SERVER_URL={q_srv}','VCAT_AGENT_MODE={agent_mode}','VCAT_MCP_URL=http://127.0.0.1:3199/mcp',('VCAT_MCP_TOKEN='+$tk),'VCAT_NODE_NAME={q_name}','VCAT_POLL_WAIT={int(poll_wait)}') | Set-Content (Join-Path $d 'vcat_agent.env') -Encoding UTF8; "
            f"& (Join-Path $d 'venv\\Scripts\\python.exe') (Join-Path $a 'vcat_agent.py') --env-file (Join-Path $d 'vcat_agent.env') --pair-token '{q_pair}' --node-name '{q_name}' --register-only; "
            "& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $a 'install-daemon.ps1')\""
        )
        return f"install-vcat-agent-{os_key}.ps1", script, one_line

    q_url = shlex.quote(download_url)
    q_srv = shlex.quote(clean_server)
    q_pair = shlex.quote(pair_code)
    q_name = shlex.quote(safe_name)
    script = f"""#!/usr/bin/env bash
set -euo pipefail
BASE_DIR="${{VCAT_STATE_DIR:-$HOME/.acquisition-vcat-agent}}"
APP_DIR="$BASE_DIR/app"
VENV_DIR="$BASE_DIR/venv"
ENV_FILE="$BASE_DIR/vcat_agent.env"
START_BIN="$BASE_DIR/start-agent.sh"
mkdir -p "$BASE_DIR" "$APP_DIR"

DOWNLOAD_URL={q_url}
SERVER_URL={q_srv}
PAIR_CODE={q_pair}
NODE_NAME={q_name}
AGENT_MODE={shlex.quote(agent_mode)}
POLL_WAIT={int(poll_wait)}

echo "[1/5] 正在下载 Agent 安装包: $DOWNLOAD_URL"
TMP_PKG="$BASE_DIR/agent-package.download"
curl -fsSL "$DOWNLOAD_URL" -o "$TMP_PKG"
if tar -xzf "$TMP_PKG" -C "$APP_DIR" >/dev/null 2>&1; then
  :
elif command -v unzip >/dev/null 2>&1; then
  unzip -qo "$TMP_PKG" -d "$APP_DIR"
else
  python3 -m zipfile -e "$TMP_PKG" "$APP_DIR"
fi
rm -f "$TMP_PKG"

echo "[2/5] 创建 Python 虚拟环境并安装依赖..."
if [ ! -x "$VENV_DIR/bin/python" ]; then
  python3 -m venv "$VENV_DIR"
fi
PY_BIN="$VENV_DIR/bin/python"
"$PY_BIN" -m pip install --quiet -r "$APP_DIR/requirements.txt"

cp -f "$APP_DIR/start-agent.sh" "$START_BIN"
chmod +x "$START_BIN" "$APP_DIR/vcat_agent.py" "$APP_DIR/install-daemon.sh"

echo "[3/5] 写入环境变量与 Agent 路径 ($ENV_FILE)..."
MCP_TOK="${{VCAT_MCP_TOKEN:-}}"
if [ -z "$MCP_TOK" ] && [ -f "$ENV_FILE" ]; then
  MCP_TOK="$(grep -E '^VCAT_MCP_TOKEN=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
fi
if [ -z "$MCP_TOK" ]; then
  read -r -p "请输入本机小V猫 MCP 令牌 (VCAT_MCP_TOKEN): " MCP_TOK
fi

cat > "$ENV_FILE" <<EOF
VCAT_SERVER_URL=$SERVER_URL
VCAT_AGENT_MODE=$AGENT_MODE
VCAT_MCP_URL=http://127.0.0.1:3199/mcp
VCAT_MCP_TOKEN=$MCP_TOK
VCAT_NODE_NAME=$NODE_NAME
VCAT_POLL_WAIT=$POLL_WAIT
VCAT_STATE_DIR=$BASE_DIR
ACQ_VCAT_AGENT_HOME=$APP_DIR
ACQ_VCAT_AGENT_BIN=$START_BIN
ACQ_VCAT_AGENT_PYTHON=$PY_BIN
EOF
chmod 600 "$ENV_FILE"

export VCAT_SERVER_URL="$SERVER_URL"
export ACQ_VCAT_AGENT_HOME="$APP_DIR"
export ACQ_VCAT_AGENT_BIN="$START_BIN"
export ACQ_VCAT_AGENT_PYTHON="$PY_BIN"

echo "[4/5] 执行一次性配对注册并持久化本机环境变量..."
"$PY_BIN" "$APP_DIR/vcat_agent.py" --env-file "$ENV_FILE" --mode "$AGENT_MODE" --server "$SERVER_URL" --pair-token "$PAIR_CODE" --node-name "$NODE_NAME" --register-only

echo "[5/5] 安装 OS 常驻守护并自动启动 Agent..."
bash "$APP_DIR/install-daemon.sh"
"""
    one_line = (
        f"mkdir -p ~/.acquisition-vcat-agent/app && "
        f"curl -fsSL {q_url} -o ~/.acquisition-vcat-agent/pkg.tar.gz && "
        f"tar -xzf ~/.acquisition-vcat-agent/pkg.tar.gz -C ~/.acquisition-vcat-agent/app && "
        f"python3 -m venv ~/.acquisition-vcat-agent/venv && "
        f"~/.acquisition-vcat-agent/venv/bin/python -m pip install -q -r ~/.acquisition-vcat-agent/app/requirements.txt && "
        f"cp ~/.acquisition-vcat-agent/app/start-agent.sh ~/.acquisition-vcat-agent/start-agent.sh && "
        f"chmod +x ~/.acquisition-vcat-agent/start-agent.sh ~/.acquisition-vcat-agent/app/install-daemon.sh && "
        f"VCAT_SERVER_URL={q_srv} VCAT_AGENT_MODE={shlex.quote(agent_mode)} VCAT_NODE_NAME={q_name} VCAT_POLL_WAIT={int(poll_wait)} "
        f"~/.acquisition-vcat-agent/venv/bin/python ~/.acquisition-vcat-agent/app/vcat_agent.py "
        f"--mode {shlex.quote(agent_mode)} --server {q_srv} --pair-token {q_pair} --node-name {q_name} --register-only && "
        f"bash ~/.acquisition-vcat-agent/app/install-daemon.sh"
    )
    return f"install-vcat-agent-{os_key}.sh", script, one_line


def _probe_port_open(port: int = 18899, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def try_start_local_agent(state_dir: Path | None = None) -> dict[str, Any]:
    """Attempt to start an already-installed local agent via environment variables or standard path."""
    base_dir = Path(
        state_dir
        or os.environ.get("VCAT_STATE_DIR")
        or (Path.home() / ".acquisition-vcat-agent")
    ).expanduser()
    bin_env = (os.environ.get("ACQ_VCAT_AGENT_BIN") or "").strip()
    is_win = platform.system().lower().startswith("win")
    default_bin = base_dir / ("start-agent.cmd" if is_win else "start-agent.sh")
    launch_bin = Path(bin_env).expanduser() if bin_env else default_bin
    creds_path = base_dir / "node_credentials.json"

    if _probe_port_open(18899):
        return {
            "ok": True,
            "status": "already_running",
            "message": "本机 Agent 健康端口 (127.0.0.1:18899) 已在运行中",
            "agent_bin": str(launch_bin),
            "protocol_url": f"{DEFAULT_PROTOCOL_SCHEME}://start",
        }

    if not launch_bin.is_file() or not creds_path.is_file():
        return {
            "ok": False,
            "status": "not_installed",
            "message": "当前机器尚未检测到已安装并配对的 Agent（请先运行「一键下载部署并自动启动」脚本）",
            "agent_bin": str(launch_bin),
            "protocol_url": f"{DEFAULT_PROTOCOL_SCHEME}://start",
        }

    logs_dir = base_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_handle = open(logs_dir / "local-start.log", "ab")
    try:
        if is_win:
            subprocess.Popen(
                ["cmd.exe", "/c", str(launch_bin)],
                stdout=log_handle,
                stderr=log_handle,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        else:
            subprocess.Popen(
                ["/bin/bash", str(launch_bin)],
                stdout=log_handle,
                stderr=log_handle,
                start_new_session=True,
            )
    finally:
        log_handle.close()

    return {
        "ok": True,
        "status": "started",
        "message": f"已通过环境变量入口拉起本机 Agent: {launch_bin}",
        "agent_bin": str(launch_bin),
        "protocol_url": f"{DEFAULT_PROTOCOL_SCHEME}://start",
    }
