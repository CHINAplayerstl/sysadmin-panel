# SysAdmin Panel · 系统管理面板

[中文](README.md) | **English**

> A browser-based system administration panel for your own Windows machine.
> Process management · Power control · Screen monitoring · File browser · Clipboard · Startup items · Command execution · Live metrics.
>
> Flask backend, vanilla JS + ECharts frontend. **Very few dependencies, works offline**, no Group Policy required — runs fine on Windows 10 Home.

---

## ⚠️ Security warning — read this first

This panel can **kill processes, execute commands, view your screen, download any file, and shut down or reboot the machine**.
Its access token is, in practice, **full control over the host**.

- It listens on **`127.0.0.1` by default**. Before exposing it to your LAN, set a **long random token**.
- There is a hard gate in the code: if `host` is not a loopback address and **no token is configured**, the service **refuses to start**.
- **Never port-forward or tunnel this to the public internet.**
- If you install it as a background service (running as `SYSTEM`), the command-execution endpoint becomes
  **arbitrary command execution as SYSTEM**, and the file browser can read the entire disk. Decide for yourself whether that is acceptable.
- Released under the MIT license, **with no warranty whatsoever**. Use at your own risk.

---

## Features

| Module | Description |
|---|---|
| **Process manager** | PID / name / CPU / memory / status / user; fuzzy search, four sort fields, multi-select, batch kill. Auto-refresh every 3 s |
| **Risk grading** | Processes are graded `fatal` (killing them blue-screens Windows, e.g. `lsass`, `csrss`), `important`, `normal`. `fatal` ones are **rejected by default**; the frontend must confirm explicitly before sending `force` |
| **Power control** | Delayed shutdown / reboot / cancel / lock / hibernate / sleep — every action requires a confirmation dialog |
| **Screen monitoring** | Server grabs frames as JPEG; 1 / 2 / 0.5 / 0.33 fps selectable; manual snapshot download; fullscreen view |
| **Live metrics** | CPU / memory / disk / network up-down line charts (ECharts), fed by a background sampling thread with a history deque |
| **System info** | CPU model, core counts, total memory, OS version, uptime, per-drive capacity |
| **File browser** | Browse disks and download files (with HTTP range support); path traversal is validated |
| **Clipboard** | View the current clipboard text |
| **Startup items** | Registry `Run`/`RunOnce` (both 32- and 64-bit views) plus the two Startup folders |
| **Command execution** | Run CMD from the browser and see the output. **Whitelist mode by default** (27 read-only commands), with shell metacharacters (`& \| > < ^`) rejected |
| **Background service** | Optional: install as a boot-time `SYSTEM` service that is reachable **no matter who is logged in — or if nobody is** |

UI: dark "tech" theme, card-based dashboard, toasts, promise-based confirm dialogs, loading spinners, pure CSS Grid responsive layout.

---

## Architecture: why there is an in-session agent

The most interesting part of this project comes from a real Windows constraint.

**The conflicting requirements**: "reachable even when nobody is logged in" *and* "screen monitoring".

- To satisfy the first, the service must run as **`SYSTEM` in session 0**, independent of any interactive logon.
- But **screen capture and clipboard are session-scoped resources** — from session 0 you only get a black frame and an empty clipboard.

**The solution**: split the service from the capture work.

```
        ┌──────────── Session 0 (service session, SYSTEM) ──────────┐
        │  Panel service                                            │
        │    processes / power / files / commands / metrics   ✅ all │
        │    screen / clipboard: cannot reach them (session-scoped)  │
        └───────────────────┬───────────────────────────────────────┘
                            │  WTSQueryUserToken
                            │  + DuplicateTokenEx
                            │  + CreateProcessAsUser
                            ▼
        ┌──────────── The logged-on user's desktop session ─────────┐
        │  Agent agent.py (runs as that user)                       │
        │    captures screen / reads clipboard → POSTs to 127.0.0.1 │
        └───────────────────────────────────────────────────────────┘
```

The agent only captures while **somebody is actually watching** (it idles out 8 seconds after the
frontend stops polling), so it costs almost no CPU. When nobody is logged in, the screen card shows
a **placeholder image that explains why**, instead of a broken image.

### Why the agent is deployed to `C:\ProgramData`

The agent runs **as the currently logged-on user**, while the project usually lives under a user
profile (`C:\Users\<name>\...`) whose ACL only grants that user, `SYSTEM` and `Administrators`.

**When a different user logs in, that user cannot even enter the project directory**, so
`CreateProcessAsUser` cannot even open the interpreter — the symptom is the web UI being stuck on
"starting the in-session agent" forever.

So the service automatically deploys what the agent needs into `C:\ProgramData\sysadmin-panel\` and grants access:

| Content | Permission |
|---|---|
| `agent.py` / `screen_grab.py` | `BUILTIN\Users`: read + execute |
| `agent-config.json` (port and `agent_secret` only) | `BUILTIN\Users`: read |
| Agent log directory | `BUILTIN\Users`: write |
| Python interpreter directory | `BUILTIN\Users`: read + execute |

> The panel's `config.json` (which holds `access_token`) is **never** copied there — that token must
> not be readable by ordinary users. Deployment is idempotent and automatic, so editing `agent.py`
> does not require re-running the installer.

---

## Quick start

### 1. Dependencies

```powershell
python -m pip install -r requirements.txt
```

Python 3.10+ required (developed on 3.12). Only four dependencies: Flask, psutil, Pillow, mss.

### 2. Configuration

On first launch the program generates `config.json` and assigns a random token.
Or copy the template yourself:

```powershell
Copy-Item config.example.json config.json
```

Key options:

| Option | Meaning |
|---|---|
| `host` | `127.0.0.1` local only (default); `0.0.0.0` LAN-reachable (**requires `access_token`**) |
| `port` | Default `8787` |
| `access_token` | Access token. Empty = no authentication (dangerous). Generate with `python -c "import secrets;print(secrets.token_urlsafe(24))"` |
| `allow_arbitrary_commands` | `false` = whitelist mode (default); `true` = any command (dangerous) |
| `file_roots` | Roots the file browser may access; empty = all drives |
| `screenshot_quality` | JPEG quality for screen frames, default 70 |

### 3. Run

```powershell
python run.py                  # use host/port from config.json
python run.py --port 8788      # override the port
python run.py --host 0.0.0.0   # temporarily expose to the LAN
```

You can also double-click `start.cmd`.

If the port is already taken, the program **tells you exactly which process holds it**
(PID + name) instead of throwing a bare `WinError 10048`.

### 4. Open it

Browse to `http://127.0.0.1:8787` and enter the access token. It is stored in `localStorage`,
so you only type it once.

---

## Installing as a background service (optional)

```powershell
# Requires administrator rights: double-click install-service.cmd
```

It registers a scheduled task named `sysadmin-panel`:

| Mechanism | Effect |
|---|---|
| Boot trigger (30 s delay) | Starts automatically at boot, **nobody needs to be logged in** |
| 1-minute watchdog | Restarts it if it dies; ignores new instances while running |
| Restart on failure | Retries after 1 minute, up to 999 times |
| No execution time limit | Task Scheduler will not kill it for "timeout" |
| Principal `SYSTEM`, run level `Highest` | Runs in session 0, independent of any logon |

**Need to restart after a code or config change?** No admin console required:

```powershell
curl -X POST http://127.0.0.1:8787/api/service/restart -H "X-Auth-Token: <your token>"
```

It exits cleanly and the watchdog brings it back (measured: about 41 seconds).

> Note: a scheduled task running as `SYSTEM` is **invisible to `Get-ScheduledTask` in a
> non-elevated context**. Querying and managing it require administrator rights.

---

## API reference

| Method | Path | Description |
|---|---|---|
| GET | `/api/ping` | Validate the token |
| POST | `/api/service/restart` | Restart the service (exit + watchdog) |
| GET | `/api/system/info` | CPU model, memory, OS version, drives, boot time |
| GET | `/api/metrics` | Live metrics + history (chart data source) |
| GET | `/api/processes?search=&sort=&order=` | Process list |
| POST | `/api/processes/kill` | `{"pids":[...], "force":false}` batch kill |
| POST | `/api/power/shutdown` · `/reboot` · `/cancel` · `/lock` · `/hibernate` · `/sleep` | Power control |
| GET | `/api/power/status` | Pending action and remaining seconds |
| GET | `/api/screen/frame` · `/snapshot` · `/info` | Frame / snapshot download / status |
| GET | `/api/files/drives` · `/list?path=` · `/download?path=` | Drives / list / download |
| GET | `/api/clipboard` | Clipboard text |
| GET | `/api/startup` | Startup items |
| GET | `/api/exec/presets` · POST `/api/exec` | Command whitelist / execute |
| — | `/internal/agent/*` | Agent-only, loopback address **and** `agent_secret` required |

The token can be supplied three ways: header `X-Auth-Token`, query string `?token=`
(used by `<img>` tags), or cookie `panel_token`.

---

## Security model

Implemented protections:

1. **Listens on `127.0.0.1` by default** — nothing else on the LAN can even connect.
2. **Every `/api/*` route requires the token**, compared in constant time with `hmac.compare_digest`.
3. **Refuses to start** when bound to a non-loopback address with **no token configured**.
4. **Failure rate limiting**: 10 failed attempts from one IP within 5 minutes → 5-minute block
   (the token is the only defense, so brute-force protection is mandatory).
5. **Command whitelist by default, shell metacharacters rejected** — blocks tricks like `ipconfig & something`.
6. **File access restricted to `file_roots`**, validated with `os.path.commonpath`
   (so `C:\foo` cannot fool `C:\foobar`).
7. Security headers (`nosniff`, `SAMEORIGIN`) and `no-store` on all `/api/*` responses.
8. **Internal endpoints accept loopback connections only**, plus a separate secret.

Things you still have to handle yourself:

- **Never share the token**: no screenshots, no commits, no pasting into chat.
- For tighter security, narrow `file_roots`, e.g. `["C:\\Users\\<you>"]`.
- Flask ships a **development server**. Fine for personal use; for a long-running deployment use
  `waitress`: `pip install waitress`, then serve it yourself.

---

## War stories (real bugs, and what they taught)

**1. Screen capture from session 0 returns black — by design, not a bug**
A `SYSTEM` service lives in session 0, where `ImageGrab` / `mss` can only see a black desktop.
The correct fix is `WTSQueryUserToken` + `CreateProcessAsUser` to launch a capture helper inside
the interactive session.

**2. The agent cannot start when another user logs in — user-profile ACLs**
The project lives in `C:\Users\<A>\...`, whose ACL grants only A plus SYSTEM/Administrators.
When B logs in, B cannot even enter the directory. Fixed by deploying the agent to
`C:\ProgramData` and granting access there. (Side note: Windows grants every user
`SeChangeNotifyPrivilege` — "bypass traverse checking" — so you do **not** need to grant access to
the parent directories, only to the target path.)

**3. `os.execv` cannot be used to "restart in place" on Windows**
It actually means "spawn a new process, then terminate the old one", so the PID changes. Worse,
Flask's listening socket sets `SO_REUSEADDR`, letting the new process **bind the same port again** —
two sockets fighting over one port. The symptom is a port that shows as LISTENING while every
connection times out, leaving an orphaned socket owned by a dead PID. The correct approach is to
exit cleanly and let the scheduler watchdog bring the service back.

**4. Two `desktop.ini` Notepad windows pop up after logging in**
Explorer walks the Startup folder at logon and "launches" each entry. A normal `desktop.ini`
carries `Hidden+System` and is skipped — but once those attributes are lost it is treated as an
ordinary startup item and handed to `ShellExecute`, and the default handler for `.ini` is
`NOTEPAD.EXE`. Two Startup folders → two Notepad windows. The fix is restoring `Hidden+System`.

**5. A `SYSTEM` scheduled task "does not exist" in a non-elevated context**
`Get-ScheduledTask` simply does not list it. Elevate, or use `schtasks /query`.

**6. `Set-LocalUser -UserMayChangePassword` reports a misleading error**
On an administrator account it throws "cannot remove the last administrator", but the real error
code is **1322 `ERROR_ACCOUNT_RESTRICTION`**: Windows deliberately refuses to set
"user cannot change password" on a member of `Administrators`, because it must guarantee that an
administrator can always log on. You only see the real code by bypassing the cmdlet.

**7. `sys.stdout` is `None` under `pythonw.exe`**
With no console attached, any `print()` raises `AttributeError` and kills the service. Output must be
redirected to a log file **before importing Flask** (werkzeug's log handler binds `sys.stderr` at
import time).

---

## Project layout

```
sysadmin-panel/
├── run.py                  # Entry point: port diagnostics, safety gate, log redirection
├── config.py               # Config I/O (generates config.json + random token on first run)
├── agent.py                # In-session agent: screen capture / clipboard on the user desktop
├── screen_grab.py          # Shared capture (mss ~45 ms preferred, PIL ~530 ms fallback)
├── smoke_test.py           # 34 read-only API smoke tests
├── start.cmd               # Foreground launcher
├── install-service.ps1/.cmd# Install as a SYSTEM background service (admin required)
├── takeover-8787.ps1/.cmd  # Take over an occupied port: kill holder + add firewall rule (admin)
├── requirements.txt / config.example.json / LICENSE / .gitignore
│
├── app/                    # Backend
│   ├── __init__.py         # App factory + restart endpoint + unified error responses
│   ├── auth.py             # Token validation + failure rate limiting
│   ├── sampler.py          # Background metrics sampling thread
│   ├── wts.py              # Cross-session launching (WTS API wrappers)
│   ├── live_state.py       # Shared state between service and agent
│   ├── agent_control.py    # Agent deployment and lifecycle
│   ├── utils.py            # Process risk grading, console encoding, command runner
│   └── api_*.py            # Blueprints (system / process / power / screen / files / misc)
│
├── templates/index.html    # Single-page dashboard
└── static/
    ├── css/style.css       # Dark tech theme
    ├── js/                 # api.js / ui.js / charts.js / modules.js / main.js
    └── vendor/echarts.min.js
```

---

## Self-test

```powershell
python smoke_test.py http://127.0.0.1:8787 <your token>
```

Covers authentication, static assets, metrics growth, process search/sort, dangerous-operation
interception, screen capture and caching, file path escapes, clipboard, startup items and the
command whitelist. **Everything is read-only** — the only "dangerous" case deliberately probes the
guard logic with PID 4 and never actually kills anything.

---

## Known limitations

- Screen monitoring is **polled screenshots**, not a video stream — up to 2 fps, not suitable for watching video.
- **Cannot capture while the workstation is locked** (a Windows restriction). The UI says so
  explicitly and recovers automatically on unlock.
- Clipboard and screen only have content while **someone is logged in**.
- Command execution uses `cmd.exe` and cannot run interactive commands (`diskpart`, `ftp`, …).
- Windows only (depends on the WTS API and the registry).

---

## License

[MIT](LICENSE)
