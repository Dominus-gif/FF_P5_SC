# Installing the PS5 Stock Checker on Windows

This makes the checker run **hidden in the background**, starting automatically
every time you log into Windows, checking every 3 minutes, and sending a Telegram
alert the moment a PS5 comes back in stock.

You only do steps 1-4 once. After that it runs forever on its own.

---

## Step 1 — Install Python

1. Go to https://www.python.org/downloads/ and click the big **Download Python** button.
2. Run the installer.
3. **IMPORTANT:** On the first screen, tick the box **"Add python.exe to PATH"**
   (bottom of the window) before clicking Install. If you miss this, nothing else works.
4. Click **Install Now**, wait, then close.

Verify: open **PowerShell** (press Start, type `powershell`, Enter) and run:
```powershell
python --version
```
You should see something like `Python 3.12.x`. If it says "not recognized",
Python wasn't added to PATH — reinstall and tick the box.

## Step 2 — Install Git

1. Go to https://git-scm.com/download/win — the download starts automatically.
2. Run the installer and click **Next** through every screen (all defaults are fine).

Verify:
```powershell
git --version
```

## Step 3 — Download the script

In PowerShell, run these one at a time:
```powershell
cd $HOME\Documents
git clone https://github.com/Dominus-gif/FF_P5_SC.git
cd FF_P5_SC
pip install -r requirements.txt
```

## Step 4 — Add your credentials

The bot token is NOT in the code (the repo is public), so you add it locally.

In the same PowerShell window, in the `FF_P5_SC` folder, run:
```powershell
@"
TELEGRAM_BOT_TOKEN=PUT_TOKEN_HERE
TELEGRAM_CHAT_ID=PUT_CHAT_ID_HERE
"@ | Out-File -FilePath .env -Encoding utf8
```
Then open the file to fill in the real values:
```powershell
notepad .env
```
Replace `PUT_TOKEN_HERE` and `PUT_CHAT_ID_HERE` with the real token and group id
(ask whoever set up the bot for these), save, and close Notepad.

## Step 5 — Test it once

```powershell
python checker.py
```
You should see it check the PS5 listings and print `OUT_OF_STOCK` / `IN_STOCK`
for each, then `Cycle done at ...`. If it prints `(telegram not configured, skipping)`
your `.env` values are wrong — recheck step 4.

## Step 6 — Install the background task

```powershell
powershell -ExecutionPolicy Bypass -File .\install_task.ps1
```
That's it. It now runs hidden, 24/7, and restarts at every logon. No window appears.

---

## Everyday commands

Watch it live (Ctrl+C stops watching, not the checker):
```powershell
cd $HOME\Documents\FF_P5_SC
Get-Content checker.log -Wait -Tail 15
```

Check the task is running:
```powershell
Get-ScheduledTask -TaskName "PS5 Stock Checker" | Select-Object TaskName, State
```

Update to the latest script version:
```powershell
cd $HOME\Documents\FF_P5_SC
git checkout -- state.json
git pull
powershell -ExecutionPolicy Bypass -File .\uninstall_task.ps1
powershell -ExecutionPolicy Bypass -File .\install_task.ps1
```

Stop / remove it completely:
```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall_task.ps1
```

---

## Notes

- The PC only checks while it is **powered on**. It does not need to stay logged in
  to a specific app, but it must not be shut down or asleep. Sleep pauses it;
  it resumes on wake.
- Running this on multiple devices (your PC, a phone, a friend's PC) is fine and
  gives redundancy — you may just get the same alert from each within a few minutes.
- Alerts only fire for genuine PS5 consoles (any variant). Accessories are filtered
  out and verified against the real product page before alerting.
