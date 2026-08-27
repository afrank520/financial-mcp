#!/usr/bin/env bash
#
# install.sh — one-command setup for the Monarch MCP server (macOS).
#
# What it does, in order:
#   1. Checks you have Python 3.12+
#   2. Creates an isolated virtual environment (.venv) in this folder
#   3. Installs the server and its dependencies into it
#   4. Downloads the small browser used only for Monarch sign-in
#   5. Creates your config.yaml (set to live Monarch data, read-only to start)
#   6. Signs you in to Monarch (opens a browser)
#   7. Offers to connect it to the Claude Desktop app for you
#
# It NEVER sends anything anywhere. Everything happens on this Mac. Read it
# top to bottom before running if you like — that's the whole point of shipping
# a script you run locally instead of a "curl | bash" one-liner.
#
# Usage:  ./install.sh      (run it from inside this folder)

set -euo pipefail

# Always operate on the folder this script lives in, no matter where it's run from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV="$SCRIPT_DIR/.venv"
PY="$VENV/bin/python"
SERVER_LABEL="monarch"   # cosmetic nickname for the connection in Claude
CLAUDE_CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"

say()  { printf '\n\033[1m%s\033[0m\n' "$1"; }     # bold heading
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }  # green check
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }  # yellow bang
die()  { printf '\n\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

# ── 1. Python 3.12+ ──────────────────────────────────────────────────────────
say "1/7  Checking Python…"
PYBIN="$(command -v python3 || true)"
[ -n "$PYBIN" ] || die "Python 3 not found. Install it from https://www.python.org/downloads/ then re-run."
"$PYBIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)' \
  || die "Python 3.12+ required (found $("$PYBIN" --version)). Get the latest from https://www.python.org/downloads/ and re-run."
ok "$("$PYBIN" --version) at $PYBIN"

# ── 2. Virtual environment ───────────────────────────────────────────────────
say "2/7  Creating the virtual environment…"
if [ -x "$PY" ]; then
  ok "Reusing existing .venv"
else
  "$PYBIN" -m venv "$VENV"
  ok "Created .venv"
fi

# ── 3. Install the server ────────────────────────────────────────────────────
say "3/7  Installing the server and its parts (this takes a minute or two)…"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -e .
ok "Installed"

# ── 4. Sign-in browser ───────────────────────────────────────────────────────
say "4/7  Downloading the sign-in browser (one-time)…"
"$PY" -m playwright install chromium >/dev/null
ok "Ready"

# ── 5. Config ────────────────────────────────────────────────────────────────
say "5/7  Setting up your config…"
if [ -f "$SCRIPT_DIR/config.yaml" ]; then
  warn "config.yaml already exists — leaving your settings untouched."
else
  cp "$SCRIPT_DIR/config.example.yaml" "$SCRIPT_DIR/config.yaml"
  "$PY" - "$SCRIPT_DIR/config.yaml" <<'PY'
import pathlib, re, sys
p = pathlib.Path(sys.argv[1])
p.write_text(re.sub(r'(?m)^source:\s*csv\s*$', 'source: live', p.read_text()))
PY
  ok "Created config.yaml (live Monarch data, write_mode stays \"off\" = read-only)"
fi

# ── 6. Monarch sign-in ───────────────────────────────────────────────────────
say "6/7  Signing in to Monarch…"
cat <<'EOF'
  A browser window will open on the Monarch sign-in page.
    • Sign in with your EMAIL and PASSWORD (not the "Sign in with Google" button —
      Google sign-in is blocked for automated login).
    • Only sign in with Google normally? Add an email/password to your Monarch
      account first (account settings → security, or "Forgot password?"). It does
      NOT disable Google — your website and mobile logins keep working.
EOF
printf '  Press Enter when you are ready… '
read -r _
if "$VENV/bin/financial-mcp-login"; then
  ok "Signed in"
else
  warn "Sign-in did not complete. You can re-run it later with: $VENV/bin/financial-mcp-login"
fi

# ── 7. Connect Claude Desktop ────────────────────────────────────────────────
say "7/7  Connecting the Claude Desktop app…"
CMD_PATH="$VENV/bin/financial-mcp"
CFG_PATH="$SCRIPT_DIR/config.yaml"

print_block() {
  cat <<EOF

  Add this to your Claude Desktop config, under "mcpServers":

  "$SERVER_LABEL": {
    "command": "$CMD_PATH",
    "env": { "FINANCIAL_MCP_CONFIG": "$CFG_PATH" }
  }

  Config file location:
  $CLAUDE_CONFIG
EOF
}

wire_up() {
  # Safe merge via Python: preserves any other servers; refuses to clobber an
  # unreadable file; backs up before writing.
  CLAUDE_CONFIG="$CLAUDE_CONFIG" LABEL="$SERVER_LABEL" CMD_PATH="$CMD_PATH" CFG_PATH="$CFG_PATH" \
  "$PY" <<'PY'
import json, os, pathlib, shutil, time

path = pathlib.Path(os.environ["CLAUDE_CONFIG"])
label, cmd, cfg = os.environ["LABEL"], os.environ["CMD_PATH"], os.environ["CFG_PATH"]

if not path.parent.exists():
    print("SKIP: Claude Desktop does not appear to be installed (config folder missing).")
    raise SystemExit(2)

data = {}
if path.exists() and path.read_text().strip():
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        print("SKIP: existing Claude config isn't valid JSON — not touching it.")
        raise SystemExit(3)
    backup = f"{path}.backup-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, backup)
    print(f"BACKUP: {backup}")

servers = data.setdefault("mcpServers", {})
servers[label] = {"command": cmd, "env": {"FINANCIAL_MCP_CONFIG": cfg}}
path.write_text(json.dumps(data, indent=2) + "\n")
print(f"WROTE: added \"{label}\" to {path}")
PY
}

printf '  Add this to Claude Desktop automatically? A backup is made first. [y/N] '
read -r ANSWER
if [[ "$ANSWER" =~ ^[Yy]$ ]]; then
  if wire_up; then
    ok "Claude Desktop configured."
  else
    warn "Automatic setup was skipped — add it by hand instead:"
    print_block
  fi
else
  print_block
fi

# ── Done ─────────────────────────────────────────────────────────────────────
say "All set! 🎉"
cat <<'EOF'
  Next:
    1. FULLY quit the Claude Desktop app (Cmd+Q) and reopen it.
    2. In a new chat, ask:  "What are my Monarch account balances?"

  First time using it? Paste this to see everything it can do:
    "Using the Monarch MCP server, give me a short menu of what you can help
     me do with my financial data, then wait for me to pick one."

  Heads-up: big questions (a whole year of transactions, all holdings) can take
  2–4 minutes on large datasets — that's normal, it's working. And Claude will
  ask you to "Approve" the first tool use — that's the safety layer, say yes.
EOF
