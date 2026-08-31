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

## Overview — the whole setup in 3 steps

Here's the entire thing at a glance. Each step is explained in full detail below —
this is just the map so you know where you're headed:

1. **Download the code** as a ZIP from the repo, unzip it, and move the folder
   somewhere you'll remember, like Documents *(Step 1)*.
2. **Open a Terminal pointed at that folder** — easiest way: in Finder,
   right-click the folder → **Services → New Terminal at Folder**. (Don't see that
   option? Type `cd` and a space in Terminal, drag the folder onto the window, and
   press Enter.) *(Step 2 has the details.)*
3. **Run the installer:**

   ```bash
   bash install.sh
   ```

   > Run it as `bash install.sh`, not `./install.sh` — a ZIP download from
   > GitHub sometimes loses the file's "this is a program" flag, and typing
   > `./install.sh` in that case fails with "permission denied." `bash
   > install.sh` always works, ZIP or not.

   This one command does everything: it fetches a known-good Python 3.12 for
   you automatically (via a small helper called `uv` — **you do not need to
   install Python yourself**, and it won't touch or conflict with any Python
   already on your Mac), builds an isolated environment, installs the server,
   downloads the small sign-in browser, writes your settings (live Monarch
   data, read-only to start), opens Monarch for you to log in, runs a quick
   self-check, and **offers to connect the Claude Desktop app for you.** When it
   finishes, fully quit Claude (`Cmd + Q`), reopen it, and ask *"What are my
   Monarch account balances?"*

That's the fast path. One thing worth knowing before you start:

- When it asks **"Add this to Claude Desktop automatically?"**, the default is
  **No** — you have to type `y` and press Enter. If you just press Enter, it skips
  that step and you'll have to connect Claude by hand later (Path A2). **Type `y`**
  to let it do the work for you.

> Why not a "paste one line from the internet" installer? Because piping a web
> script straight into your shell is exactly the kind of thing you shouldn't do.
> You download the code, then run a script you can read — safer, same result.

**Safe to re-run anytime.** If an install gets interrupted or something looks off,
just run `bash install.sh` again — it reuses what's already there and won't touch your
`config.yaml` or your saved Monarch login. To rebuild the environment from scratch
(while still keeping your settings and login), run `bash install.sh --fresh`.

Prefer to understand and do each step by hand, or did the installer hit a snag?
The **[manual fallback](#manual--developer-fallback-do-each-step-by-hand)** at the
end of Part 1 covers the same ground step by step. 👇

---

## Part 1 — One-time setup (do this once)

### Step 1: Get the code

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

### Step 2: Open a Terminal pointed at the folder

Every command you'll run needs to happen *inside* the project folder you just
moved. The easiest way to get there:

1. In **Finder**, find the project folder and **right-click** it (or Control-click
   if you don't have a right mouse button).
2. In the menu, go to **Services → New Terminal at Folder**.
3. A Terminal window opens already pointed at that folder. That's it for this step.

**Don't see "New Terminal at Folder"?** It's just switched off on some Macs — no
problem, use this instead (it always works):

1. Open the **Terminal** app (`Cmd + Space`, type `Terminal`, press `Enter`).
2. Type `cd` and then a **space** — the letters **c**, **d**, space. (`cd` is just
   short for "change to this folder.") Don't press Enter yet.
3. **Drag the project folder** from Finder and **drop it onto the Terminal
   window.** Terminal fills in the location for you automatically.
4. Now press `Enter`.

Either way, check it worked — type this and press Enter:

```bash
ls
```

`ls` lists the files in the folder Terminal is pointed at. You should see names
like `README.md`, `pyproject.toml`, and `config.example.yaml`. If you do, you're
pointed at the right folder. 👍

### Step 3: Run the installer

```bash
bash install.sh
```

Follow the prompts. It fetches Python 3.12 automatically (no manual Python
install needed), builds the environment, installs everything, downloads the
sign-in browser, writes a `config.yaml` set to live Monarch data (read-only to
start), opens a browser window for you to sign in to Monarch, runs a quick
self-check (the same one `financial-mcp-doctor` runs on its own — see
"[If something goes wrong](#if-something-goes-wrong)"), and offers to connect
the Claude Desktop app for you. See the
[Overview](#overview--the-whole-setup-in-3-steps) above for exactly what to
expect at each prompt.

When it's done signing you in to Monarch, you're through Part 1.

**Setup is done.** Now connect it to your Claude — pick your path below.

---

## Manual / developer fallback (do each step by hand)

Skip this unless you want to understand every step, or `bash install.sh` hit a
snag and you'd rather work through it by hand. This walks through exactly what
the installer automates.

### Fallback Step 1: Install Python 3.12

This tool is written in Python, a free programming language. It needs a
**mainstream** version — **Python 3.12** is the reliable choice. (The very newest
release, like 3.14, is actually *too* new: some parts of the tool don't have
ready-made packages for it yet, so the install fails trying to build them from
scratch. Stick with 3.12.)

1. Open the **Terminal** app. (Press `Cmd + Space`, type `Terminal`, press
   `Enter`. A window with a text prompt opens — this is where you type
   commands.)
2. See what you already have — copy this line, paste it into Terminal, and press
   `Enter`:

   ```bash
   python3 --version
   ```

3. Look at what it prints:
   - **`Python 3.12.x` or `3.13.x`** → 👍 you're good, go to the next fallback step.
   - **Anything else** — older (`3.11` or lower), newer (`3.14`), or
     **"command not found"** → install 3.12:
     1. Go to [python.org/downloads](https://www.python.org/downloads/).
     2. ⚠️ **Don't click the big yellow "Download Python" button** — that's the
        newest release (too new). Instead scroll down to **"Looking for a specific
        release?"**, click the latest **Python 3.12.x**, then at the bottom of
        *that* page download the **"macOS 64-bit universal2 installer."**
     3. Run the installer, then **close and reopen Terminal** and check again with
        `python3 --version`.
     4. This installer also does not activate macOS's root certificates for you —
        if you later see a certificate error, run the bundled
        **"Install Certificates.command"** from the Python folder in
        Applications (see "[If something goes wrong](#if-something-goes-wrong)").

### Fallback Step 2: Create the virtual environment and install

A **virtual environment** is a private folder that holds Python and this tool's
parts, kept separate from the rest of your Mac so nothing conflicts. It also
creates the exact command Claude will need later. Run these **one at a time**
(paste, press Enter, wait for it to finish, then do the next), from inside the
project folder (see Part 1, Step 1 and Step 2 above if you jumped straight here):

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
python3 -m pip install --only-binary=:all: -e .
```

This downloads and installs everything the tool needs. It prints a lot of lines
and takes a minute or two. When it finishes and you get your prompt back with no
red error at the very end, it worked.

> The `--only-binary=:all:` part tells Python to use **ready-made packages
> only** and never try to compile anything from scratch. Skipping it can send
> some Macs down a slow compile that fails unless you have developer tools
> installed — this keeps it simple.

```bash
python3 -m playwright install chromium
```

This downloads a small private browser the tool uses **only** to let you sign in
to Monarch. This one-time download is what makes the easy sign-in (next step)
work.

### Fallback Step 3: Turn on live Monarch data

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
   features later once you trust it. (The `db_path` / `log_file` lines are bare
   filenames on purpose — they auto-resolve under `~/.financial_mcp/`, so you
   don't need to type a full path.)
5. Save (`Cmd + S`) and close TextEdit.

### Fallback Step 4: Sign in to Monarch (one time)

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

The last step is connecting this tool to the Claude Desktop app. **Most people
already did this without realizing it** — so start here:

### A1: Did you use the installer?

**If you ran `bash install.sh` and answered _yes_ when it asked to connect Claude
Desktop** — you're already connected. There's nothing more to set up. **Skip
straight to [A3: Restart and test](#a3-restart-and-test).**

If you set everything up by hand, or you answered _no_ to that question, do
**A2** first.

### A2: Connect it by hand (only if the installer didn't do it)

You're going to give Claude two pieces of information: **where this tool lives**
and **where its settings file is.** We'll print both, then paste them into
Claude's settings.

**Step 1 — print your two locations.** In the same Terminal window from Part 1
(the one whose prompt starts with `(.venv)`), run these two lines, one at a time.
Each prints a line starting with `/Users/…`. Leave them on screen — you'll copy
them in a moment:

```bash
echo "$(pwd)/.venv/bin/financial-mcp"
```

```bash
echo "$(pwd)/config.yaml"
```

**Step 2 — open Claude's settings file.**

1. Open the **Claude Desktop** app. (Don't have it? Download it free from
   [claude.ai/download](https://claude.ai/download) and sign in.)
2. In the menu bar, go to **Claude → Settings** (or press `Cmd + ,`).
3. Click **Developer** in the sidebar, then click **Edit Config**.
4. A file called `claude_desktop_config.json` opens in a text editor.

**Step 3 — add the connection.** What you do depends on what's already in that
file:

- **If it's empty or just says `{}`** → select everything, delete it, and paste
  this in its place:

  ```json
  {
    "mcpServers": {
      "monarch": {
        "command": "PASTE-YOUR-FIRST-LINE-HERE",
        "env": {
          "FINANCIAL_MCP_CONFIG": "PASTE-YOUR-SECOND-LINE-HERE"
        }
      }
    }
  }
  ```

- **If it already has other things in it** → **don't delete anything.** Add just
  the `"monarch": { … }` block inside the existing `"mcpServers"` section, with a
  comma separating it from what's already there.

**Step 4 — fill in your two lines and save.** Replace `PASTE-YOUR-FIRST-LINE-HERE`
with the first line you printed (it ends in `/.venv/bin/financial-mcp`) and
`PASTE-YOUR-SECOND-LINE-HERE` with the second (it ends in `/config.yaml`). Keep
the quotes `"` around each one. Then save the file with **`Cmd + S`**.

> Those two lines are the whole trick — if Claude later can't find your accounts,
> it's almost always because one of them was pasted wrong. They must be the
> **full** paths you printed, quotes and all.

### A3: Restart and test

1. **Fully quit** Claude Desktop — `Cmd + Q`. (Just closing the window isn't
   enough; it has to fully quit and reopen to load the connection.)
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

**Not sure what's wrong? Run the one-command health check first.**

```bash
.venv/bin/financial-mcp-doctor
```

(Or just `financial-mcp-doctor` if your Terminal prompt shows `(.venv)`.) It
checks your Python version, that your config paths resolve and are writable,
that a secure connection to Monarch works, and that your saved sign-in session
is still valid — and tells you exactly which one is wrong.

**`permission denied` when running the installer.**
Run it as `bash install.sh` instead of `./install.sh` — ZIP downloads from
GitHub sometimes lose the file's executable flag, and `bash install.sh` works
regardless.

**Certificate / secure-connection error** (`CERTIFICATE_VERIFY_FAILED` or
`financial-mcp-doctor` reports the secure-connection check failed).
Run `.venv/bin/financial-mcp-doctor` to confirm that's the issue. If you installed Python
by hand from python.org (the [manual fallback](#manual--developer-fallback-do-each-step-by-hand)
above) rather than through `bash install.sh`, that installer doesn't turn on
macOS's root certificates for you — open **Macintosh HD → Applications →
Python 3.12** and double-click **"Install Certificates.command,"** then try
again. (Installs via `bash install.sh` don't hit this — the bundled `certifi`
package handles it automatically.)

**Claude says it doesn't have a "monarch" tool / can't see your accounts.**
The most common cause is a wrong path in the settings file. Re-run the two `echo`
commands from **[A2, Step 1](#a2-connect-it-by-hand-only-if-the-installer-didnt-do-it)**
and make sure those *exact* lines are in `claude_desktop_config.json`. The
`command` line must end in `/.venv/bin/financial-mcp`. Save, then **fully quit**
Claude (`Cmd + Q`) and reopen. (This missing-full-path issue is the #1 first-time
snag.)

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
Re-running `bash install.sh` is always safe — it picks up where it left off and
leaves your `config.yaml` and Monarch login alone. To rebuild the environment
from scratch (say the `.venv` got into a weird state), run:

```bash
bash install.sh --fresh
```

That deletes and recreates `.venv` only; your settings and saved login are kept.

**Still stuck?** The more technical reference is in
[`docs/INSTALL.md`](docs/INSTALL.md), including the manual "copy-as-cURL" sign-in
fallback if the browser login can't run on your machine.
