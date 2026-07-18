# Install — Monarch MCP (Plan 1, CSV mode)

## 1. Install
```bash
python -m pip install -e ".[dev]"
```

## 2. Configure
```bash
cp config.example.yaml config.yaml
```
Copy your Monarch CSV exports somewhere git-ignored (e.g. `~/.financial_mcp/`)
and set `csv_transactions_path` and `csv_balances_path` in `config.yaml`.
**Never commit real CSVs or `config.yaml`.**

## 3. Connect the Claude Desktop app
Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "monarch": {
      "command": "financial-mcp",
      "env": { "FINANCIAL_MCP_CONFIG": "/absolute/path/to/config.yaml" }
    }
  }
}
```
Restart the Claude Desktop app. Ask: "What are my account balances?"

## Plan 1 scope
Read-only, offline, over your CSV exports. Live Monarch sync (Plan 2),
deeper analytics (Plan 3), and writes (Plan 4) come next.

## Plan 2 — Live Monarch sync

### 1. Log in once (terminal, not Claude)
```bash
financial-mcp-login
```
Choose **1** and paste the token from the `Authorization: Token XXXX` request
header in a logged-in Monarch browser tab (DevTools → Network → any
`api.monarch.com` request). Or choose **2** for email + password + MFA. Only the
resulting session token is stored, in your macOS Keychain — never your password.

To log out: `financial-mcp-login --logout`.

### 2. Switch config to live
In `config.yaml`:
```yaml
source: live
```

### 3. Manual smoke test (once, assumes a valid session)
```bash
FINANCIAL_MCP_CONFIG=config.yaml python -c "
from financial_mcp.config import load_config
from financial_mcp.monarch import auth
from financial_mcp.monarch.community_adapter import CommunityAdapter
cfg = load_config('config.yaml')
a = CommunityAdapter(auth.load_token(cfg.keyring_service_name), cfg.keyring_service_name)
print('accounts:', [x.name for x in a.get_accounts()])
print('transactions:', len(a.get_transactions()))
"
```
Expected: your real account names and a transaction count. **If the field names
in the output look wrong**, the real GraphQL response shape differs from the
mappers in `community_adapter.py` — adjust `_to_account` / `_to_transaction`
there and re-run `pytest tests/test_community_adapter.py`.

If the token has expired, tools return a clear "re-run `financial-mcp-login`"
message and the server keeps serving cached data with its age.
