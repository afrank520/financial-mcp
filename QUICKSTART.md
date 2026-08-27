# Quick Start — from zero to asking Claude about your money

This is the hold-your-hand guide. If you have never opened a terminal in your
life, you are in the right place. Follow the steps in order and you'll be asking
Claude about your finances in about 10–15 minutes.

**This guide is for Mac.** There are two ways to use this tool, depending on
which Claude you have:

- **Claude Desktop app** (the one you download and click on) → **[Path A](#path-a--claude-desktop-app-recommended-for-most-people)**.
  This is what most people want. It's explained in full detail, step by step.
- **Claude Code** (the developer tool you run in a terminal) → **[Path B](#path-b--claude-code)**.
  If you use Claude Code, you already know your way around a terminal, so that
  section is short — just the commands to copy.

Do the **[one-time setup](#part-1--one-time-setup-do-this-once)** first (it's the
same for both), then jump to your path.

---

## ⚡ Fastest path: the installer

Rather not do the steps by hand? There's a script that does almost everything.

1. Do **Steps 1–3** of Part 1 below: check Python, get the code, and open
   Terminal *inside* the project folder.
2. Then, instead of Steps 4–7, just run this one command:

   ```bash
   ./install.sh
   ```

It sets up the environment, installs everything, signs you in to Monarch, and
**offers to connect the Claude Desktop app for you** (it backs up your existing
config first). Follow its prompts, then restart Claude and ask a question.

> Why not a "paste this one line from the internet" installer? Because piping a
> web script straight into your shell is exactly the kind of thing you shouldn't
> do. You download the code, then run a script you can read — safer, same result.

**Safe to re-run anytime.** If an install gets interrupted or something looks
off, just run `./install.sh` again — it reuses what's already there and won't
touch your `config.yaml` or your saved Monarch login. To rebuild the environment
from scratch (while still keeping your settings and login), run
`./install.sh --fresh`.

Prefer to understand each step (or the script hit a snag)? Just follow Part 1 by
hand. 👇

---

## Part 1 — One-time setup (do this once)

### Step 1: Make sure you have Python 3.12 or newer

This tool is written in Python, a free programming language your Mac may already
have.

1. Open the **Terminal** app. (Press `Cmd + Space`, type `Terminal`, press
   `Enter`. A window with a text prompt opens — this is where you type
   commands.)
2. Copy this line, paste it into Terminal, and press `Enter`:

   ```bash
   python3 --version
   ```

3. Look at what it prints:
   - **`Python 3.12.x` or higher** (e.g. `3.13`) → 👍 you're good, go to Step 2.
   - **`Python 3.11.x` or lower**, or **"command not found"** → go to
     [python.org/downloads](https://www.python.org/downloads/), download the
     latest version for macOS, run the installer, then **close and reopen
     Terminal** and try the command again.

### Step 2: Get the code

1. Go to the project page: **https://github.com/afrank520/financial-mcp**
   (you'll need to be signed in to GitHub with access — this is a private repo).
2. Click the green **`< > Code`** button, then **Download ZIP**.
3. Find the downloaded file in your **Downloads** folder and **double-click it**
   to unzip. You'll get a folder named `financial-mcp` or `financial-mcp-main`.
4. **Move that folder somewhere you'll remember** — for example, drag it to your
   **Documents** folder. (Don't leave it in Downloads, which some people clear
   out automatically.)

> Prefer git? `git clone https://github.com/afrank520/financial-mcp.git` instead
> of steps 1–3. The rest is identical.

### Step 3: Go into the folder in Terminal

You need to tell Terminal to "work inside" that folder. There's an easy trick so
you don't have to type the path by hand:

1. In Terminal, type `cd ` — that's the letters **c**, **d**, then a **space**.
   Don't press Enter yet.
2. **Drag the project folder** from Finder and **drop it onto the Terminal
   window.** Terminal fills in the full path for you.
3. Now press `Enter`.

To confirm you're in the right place, run:

```bash
ls
```

You should see files like `README.md`, `pyproject.toml`, and
`config.example.yaml` listed. If you do, you're in the right folder. 👍

### Step 4: Create the virtual environment and install

A **virtual environment** is a private folder that holds Python and this tool's
parts, kept separate from the rest of your Mac so nothing conflicts. It also
creates the exact command Claude will need later. Run these **one at a time**
(paste, press Enter, wait for it to finish, then do the next):

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```

After that second command, your Terminal prompt should now start with
**`(.venv)`**. That means the environment is active. (You'll need to run that
`source .venv/bin/activate` line again any time you open a fresh Terminal to work
on this — but for normal use through Claude, you won't need to.)

```bash
python3 -m pip install -e .
```

This downloads and installs everything the tool needs. It prints a lot of lines
and takes a minute or two. When it finishes and you get your prompt back with no
red error at the very end, it worked.

```bash
python3 -m playwright install chromium
```

This downloads a small private browser the tool uses **only** to let you sign in
to Monarch. This one-time download is what makes the easy sign-in (next step)
work.

### Step 5: Turn on live Monarch data

1. Make your own settings file by copying the example:

   ```bash
   cp config.example.yaml config.yaml
   ```

2. Open your new settings file in TextEdit:

   ```bash
   open -e config.yaml
   ```

3. Find the line near the bottom that says:

   ```yaml
   source: csv
   ```

   Change it to:

   ```yaml
   source: live
   ```

4. **Leave everything else alone.** In particular, leave `write_mode: "off"` as
   it is — that keeps the tool **read-only** (it can look at your money but can't
   change anything), which is the safe way to start. You can turn on the cleanup
   features later once you trust it.
5. Save (`Cmd + S`) and close TextEdit.

### Step 6: Sign in to Monarch (one time)

Monarch has no public developer access, so the tool signs in the same way you do
— through a browser. It only ever keeps two small session cookies, **never your
password**.

> **Do you only ever sign in to Monarch with Google?** Then you'll need to add an
> **email-and-password** login to your Monarch account before this step will work
> — an automated local sign-in can't use Google's button. Here's the reassuring
> part: **this does not replace or turn off Google sign-in.** Monarch keeps both
> methods on the same account, so you can keep logging into the Monarch **website
> and mobile app with Google exactly as you do today** — nothing about that
> changes. The email/password is only so this tool can sign in here on your Mac.
> To add one: in Monarch, open your **account settings → security** and set a
> password, or use the **"Forgot password?"** link on the Monarch login page with
> your account email. Then use that email + password below.

Run:

```bash
financial-mcp-login
```

A browser window opens on the Monarch sign-in page.

- **Sign in with your email and password.** Use the normal email/password form.
  ⚠️ **Do not** click the "Sign in with Google" button — Monarch blocks that for
  automated browsers, and the sign-in will hang.
- Once you're fully logged in and see your Monarch dashboard, the tool grabs the
  session automatically and prints **"Signed in — session saved."** You can close
  the browser window.

> Taking too long (especially with two-factor codes)? Give yourself more time:
> `FINANCIAL_MCP_LOGIN_TIMEOUT=600 financial-mcp-login`

**Setup is done.** Now connect it to your Claude — pick your path below.

---

## Path A — Claude Desktop app (recommended for most people)

You'll tell the Claude Desktop app how to start this tool. To do that, Claude
needs **two exact file paths** from your Mac. Let's print them so you can just
copy them.

### A1: Get your two paths

In the **same Terminal window** (still showing `(.venv)` and still inside the
project folder), run these two lines and **keep the window open** — you'll copy
the results in a moment:

```bash
echo "$(pwd)/.venv/bin/financial-mcp"
```

```bash
echo "$(pwd)/config.yaml"
```

Each prints a line starting with `/Users/...`. The first is the **command path**,
the second is the **config path**. Leave them on screen.

### A2: Open Claude's config file

1. Open the **Claude Desktop** app. (Don't have it? Download it free from
   [claude.ai/download](https://claude.ai/download) and sign in.)
2. In the menu bar, go to **Claude → Settings** (or press `Cmd + ,`).
3. Click **Developer** in the sidebar, then click **Edit Config**.
4. This opens a file called `claude_desktop_config.json` in a text editor. It's
   either empty, says `{}`, or already has some content.

### A3: Paste in the connection

Replace the entire contents of that file with the block below — then swap in your
two paths from Step A1:

```json
{
  "mcpServers": {
    "monarch": {
      "command": "/Users/you/Documents/financial-mcp/.venv/bin/financial-mcp",
      "env": {
        "FINANCIAL_MCP_CONFIG": "/Users/you/Documents/financial-mcp/config.yaml"
      }
    }
  }
}
```

- Replace the `"command"` value with your **first** path (the `.venv/bin/...`
  one).
- Replace the `FINANCIAL_MCP_CONFIG` value with your **second** path (the
  `config.yaml` one).
- Keep the quotes `"` and the exact layout. Then **save** the file (`Cmd + S`).

> Already have other things in `mcpServers`? Don't wipe them — just add the
> `"monarch": { ... }` piece alongside what's there, separated by a comma.

### A4: Restart and test

1. **Fully quit** Claude Desktop — `Cmd + Q`. (Just closing the window isn't
   enough; it has to fully quit and reopen to load the new setting.)
2. Reopen Claude Desktop.
3. In a **new chat**, ask:

   > **What are my Monarch account balances?**

If Claude comes back with your real accounts and balances, 🎉 **you're done.**

Jump to **[Your first conversation](#your-first-conversation)** for what to try
and what to expect, or **[If something goes wrong](#if-something-goes-wrong)** if
it didn't work.

---

## Path B — Claude Code

You've done Part 1 (so you have `.venv`, an installed package, a `config.yaml`
with `source: live`, and a saved Monarch session). From inside the project
folder, with the virtual environment active, register the server — this uses
`$(pwd)` to fill in absolute paths for you:

```bash
claude mcp add monarch \
  --env FINANCIAL_MCP_CONFIG="$(pwd)/config.yaml" \
  -- "$(pwd)/.venv/bin/financial-mcp"
```

Confirm it registered:

```bash
claude mcp list
```

You should see `monarch` listed. Then start (or restart) Claude Code and ask:

> What are my Monarch account balances?

That's it. The absolute paths matter — Claude Code launches the server outside
your shell, so `financial-mcp` alone (without the full `.venv/bin/` path) won't
be found.

---

## Your first conversation

Once Claude is answering with your real data, here's how to get oriented — plus a
few things to know so you don't second-guess whether it's working. (These are the
exact things that made *me* wonder the first time.)

**Start by asking what it can do.** Paste this into a new chat:

> Using the Monarch MCP server, give me a short menu of what you can help me do
> with my financial data — the kinds of questions I can ask, and the actions you
> can take. Then wait for me to pick one.

**Then try one or two small questions** — these come back quickly:

> What are my current account balances?

> What's my net worth right now?

### A few things that are completely normal

- **⏳ Big questions can take 2–4 minutes.** If you ask something that spans a lot
  of history — every transaction this year, spending across many months, all your
  investment holdings — the server has to pull and crunch a large number of
  records on your Mac. On large datasets that can take **two to four minutes.** It
  has *not* frozen; it's working. Narrow questions (one month, one category,
  current balances) come back fast.

- **✅ "Approve" / "Allow" pop-ups are the safeguards working.** The first time
  Claude uses one of these tools, the app asks you to **approve** it. That's not
  an error — it's the safety design doing its job: nothing touches your Monarch
  data until you say yes. Go ahead and approve (you can allow it for the whole
  chat). Later, if you turn on the cleanup features, any change that actually
  *edits* your data asks for a second, explicit confirmation on top of this —
  again, on purpose.

- **💻 It only works on this Mac, while it's running.** The server runs locally
  and your Monarch session lives in a secured file on this computer, so Claude can
  use these tools only here. The **Claude mobile app can't use them** — your phone
  can't reach a program running on your Mac. That's by design: your financial data
  never leaves your machine.

### More to ask once you're comfortable

- "What's my net worth, and how has it changed over the last 6 months?"
- "How much did I spend on groceries last month?"
- "What are my recurring subscriptions and what do they total?"
- "Show me my savings rate for June and where the money went."
- "What's my budget status this month — am I over anywhere?"

Everything is computed on your machine and answered penny-accurately.

---

## If something goes wrong

**Claude says it doesn't have a "monarch" tool / can't see your accounts.**
The most common cause is a wrong path in the config. Re-run the two `echo`
commands from Step A1 and make sure those *exact* strings are in
`claude_desktop_config.json`. The `command` path must end in
`/.venv/bin/financial-mcp`. Save, then **fully quit** Claude (`Cmd + Q`) and
reopen. (This missing-full-path issue is the #1 first-time snag.)

**Claude answers but says the session expired / asks you to log in again.**
Monarch sessions expire after a while. Just re-run the sign-in:

```bash
financial-mcp-login
```

(Run it from inside the project folder with `source .venv/bin/activate` first if
your prompt doesn't show `(.venv)`.)

**The sign-in browser window just hangs.**
You probably clicked "Sign in with Google" — that's blocked. Close the window,
run `financial-mcp-login` again, and use the **email + password** form instead.

**`financial-mcp: command not found` when signing in.**
Your virtual environment isn't active. Make sure your Terminal prompt shows
`(.venv)`; if not, `cd` into the project folder and run
`source .venv/bin/activate`, then try again.

**An install got interrupted, or you just want a clean slate.**
Re-running `./install.sh` is always safe — it picks up where it left off and
leaves your `config.yaml` and Monarch login alone. To rebuild the environment
from scratch (say the `.venv` got into a weird state), run:

```bash
./install.sh --fresh
```

That deletes and recreates `.venv` only; your settings and saved login are kept.

**Still stuck?** The more technical reference is in
[`docs/INSTALL.md`](docs/INSTALL.md), including the manual "copy-as-cURL" sign-in
fallback if the browser login can't run on your machine.
