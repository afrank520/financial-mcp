# How to update Monarch MCP

Getting the latest version is quick and **safe**. Read the one-minute
reassurance first, then follow the short steps for your setup.

---

## First, the important part: you won't lose anything

Your **Monarch sign-in and your data live in a separate folder** from the app's
code (`~/.financial_mcp/` in your home directory). Updating only replaces the
**code** — it never touches that folder.

So when you update, you will **not**:

- ❌ have to sign in to Monarch again
- ❌ lose your settings or your history

You're just swapping in newer code. That's it.

---

## Which kind of setup do you have?

**Answer one question:** when you first set this up, did you *download a ZIP
file*, or did you *clone it with git*?

- If someone sent you a **ZIP file** → follow **Path A**.
- If you used **git** (you ran a `git clone` command) → follow **Path B**.

If you're not sure, you have the ZIP setup. Use **Path A**.

---

## Path A — You have the ZIP version

1. **Download the new ZIP** (the one you were sent) and unzip it.

2. **Replace your old files with the new ones**, keeping the **same folder** you
   already use. The easiest way: open the new unzipped folder, select
   everything inside it, and drag it into your existing Monarch MCP folder,
   choosing **"Replace"** when asked.

   > 👉 Keep using the *same folder in the same place*. Don't make a brand-new
   > folder somewhere else — that's what causes headaches.

3. **Open Terminal**, and run the installer from that folder. To do this, type
   `cd ` (with a space after it), then drag your Monarch MCP folder onto the
   Terminal window and press **Return**. Then run:

   ```bash
   ./install.sh
   ```

   It reuses what you already have and keeps your login and settings. Just
   follow any prompts.

4. **Fully quit Claude Desktop and reopen it.** Press **Cmd + Q** to quit
   (closing the window is not enough), then open it again.

✅ Done — you're on the latest version.

---

## Path B — You have the git version (recommended for the future)

1. **Open Terminal** and go to your Monarch MCP folder. Type `cd ` (with a space),
   drag the folder onto Terminal, and press **Return**.

2. **Get the latest code and re-run the installer:**

   ```bash
   git pull
   ./install.sh
   ```

3. **Fully quit Claude Desktop and reopen it** (**Cmd + Q**, then reopen).

✅ Done. From now on, updating is always just those two commands.

---

## The two rules that always apply

No matter which path you used, these are the two things people forget:

1. **Always run `./install.sh` after updating.** Don't just swap the files and
   stop. The installer re-links everything so the new code actually runs.
2. **Always fully quit Claude Desktop (Cmd + Q) and reopen it.** If you skip
   this, it keeps running the *old* version from memory and it'll look like the
   update didn't work.

---

## "Something looks off"

- **It still behaves like the old version.** You probably didn't fully quit
  Claude Desktop. Press **Cmd + Q** (not just close the window), reopen, and try
  again.
- **It asks you to sign in to Monarch again.** That's rare and just means your
  saved session expired — it's not caused by the update. Sign in as usual.
- **Terminal shows an error during `./install.sh`.** Try
  `./install.sh --fresh`, which rebuilds the environment from scratch while
  **still keeping your login and settings**.
- **Still stuck?** Ask the person who shared this with you — the exact error
  text from Terminal helps them help you fast.

---

## Want the easy path forever? Switch to git.

If you're on the ZIP version and tired of downloading files, the git version
lets you update with just `git pull` — no more zips. Ask the person who shared
this to give you access, and they can walk you through the one-time setup.
