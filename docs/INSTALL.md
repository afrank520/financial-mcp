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
