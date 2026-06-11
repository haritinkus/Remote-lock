# Remote Machine Lock Tool

A Python command-line tool that remotely and silently locks the screen on a batch of machines over SSH. Supports **Windows**, **Linux**, and **macOS** targets from a single script — no agent installation required on target machines.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Enabling SSH on Target Machines](#enabling-ssh-on-target-machines)
   - [Windows](#windows-openssh-server)
   - [Linux](#linux)
   - [macOS](#macos)
4. [Setting Up SSH Keys (Recommended)](#setting-up-ssh-keys-recommended)
5. [Configuration](#configuration)
6. [hosts.txt Format](#hoststxt-format)
7. [Usage](#usage)
8. [Example Output](#example-output)
9. [Audit Log](#audit-log)
10. [Troubleshooting](#troubleshooting)

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.10+ | On the machine running this script |
| `paramiko` library | Installed via `requirements.txt` |
| SSH server on each target | See setup steps below |
| Admin/sudo account on targets | Required to issue the lock command |

---

## Installation

```bash
# 1. Clone or copy this project to your machine
cd remote-lock-tool

# 2. Install the only dependency
pip install -r requirements.txt
```

---

## Enabling SSH on Target Machines

### Windows (OpenSSH Server)

OpenSSH is built into Windows 10 (1809+) and Windows 11. Run the following in an **elevated PowerShell**:

```powershell
# Install OpenSSH Server
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0

# Start the service now
Start-Service sshd

# Set it to start automatically on boot
Set-Service -Name sshd -StartupType Automatic

# Allow SSH through the firewall (usually added automatically)
New-NetFirewallRule -Name sshd -DisplayName 'OpenSSH Server (sshd)' `
    -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
```

Verify it works:
```powershell
Get-Service -Name sshd
```

> **Note:** The lock command (`rundll32.exe user32.dll,LockWorkStation`) only locks the session of the **currently logged-in user** on that machine. If no user is logged in interactively, the command has no visible effect.

### Linux

Most distributions ship with `openssh-server`. Install and enable it:

```bash
# Debian / Ubuntu
sudo apt update && sudo apt install -y openssh-server
sudo systemctl enable --now ssh

# RHEL / CentOS / Fedora
sudo dnf install -y openssh-server
sudo systemctl enable --now sshd
```

The lock command used is `loginctl lock-session`, which requires an active graphical session (GNOME, KDE, etc.). A fallback to `xdg-screensaver lock` is also attempted.

### macOS

macOS ships with SSH but it is disabled by default. Enable it via:

- **System Settings → General → Sharing → Remote Login** — toggle ON

Or via Terminal:
```bash
sudo systemsetup -setremotelogin on
```

---

## Setting Up SSH Keys (Recommended)

Using an SSH key avoids storing passwords anywhere and is significantly more secure.

### Step 1 — Generate a key pair (on your admin machine)

```bash
ssh-keygen -t ed25519 -C "remote-lock-admin" -f ~/.ssh/lock_key
```

This creates:
- `~/.ssh/lock_key` — private key (keep this secret, never share)
- `~/.ssh/lock_key.pub` — public key (copy to target machines)

### Step 2 — Copy the public key to each target

**Linux / macOS targets:**
```bash
ssh-copy-id -i ~/.ssh/lock_key.pub admin@192.168.1.10
```

Or manually:
```bash
# On the target machine, append the public key
cat ~/.ssh/lock_key.pub >> ~/.ssh/authorized_keys
chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys
```

**Windows targets:** Append the public key content to:
```
C:\Users\<username>\.ssh\authorized_keys
```
For **administrator** accounts, Windows requires it in a different location:
```
C:\ProgramData\ssh\administrators_authorized_keys
```
Then set correct permissions (run in elevated PowerShell):
```powershell
$acl = Get-Acl "C:\ProgramData\ssh\administrators_authorized_keys"
$acl.SetAccessRuleProtection($true, $false)
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule("SYSTEM","FullControl","Allow")
$acl.SetAccessRule($rule)
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule("Administrators","FullControl","Allow")
$acl.SetAccessRule($rule)
Set-Acl "C:\ProgramData\ssh\administrators_authorized_keys" $acl
```

### Step 3 — Update config.py

```python
SSH_KEY_PATH = "~/.ssh/lock_key"
```

### Step 4 — Test

```bash
ssh -i ~/.ssh/lock_key admin@192.168.1.10
```

---

## Configuration

Edit `config.py` before running the script:

| Setting | Default | Description |
|---|---|---|
| `SSH_USERNAME` | `"admin"` | Default SSH user for hosts that don't specify one |
| `SSH_KEY_PATH` | `"~/.ssh/id_rsa"` | Path to your SSH private key (`~` is expanded automatically) |
| `SSH_PORT` | `22` | SSH port (change if your network uses a non-standard port) |
| `SSH_TIMEOUT` | `10` | Seconds to wait before marking a host unreachable |
| `MAX_WORKERS` | `10` | How many machines to lock in parallel |
| `HOSTS_FILE` | `"hosts.txt"` | Path to the hosts list file |
| `TRUST_NEW_HOSTS` | `False` | Host key verification policy — see below |
| `AUDIT_LOG_FILE` | `"lock_audit.log"` | Path to the audit log file |
| `AUDIT_LOG_MAX_BYTES` | `10485760` (10 MB) | Rotate the log when it exceeds this size; `0` disables size rotation |
| `AUDIT_LOG_BACKUP_COUNT` | `7` | Number of rotated backup files to keep (e.g. `lock_audit.log.1` … `.7`) |
| `AUDIT_LOG_MAX_DAYS` | `90` | Delete backup files older than this many days; `0` disables age cleanup |

### Host key verification (`TRUST_NEW_HOSTS`)

**`TRUST_NEW_HOSTS = False` (default — secure):** The script loads your local `~/.ssh/known_hosts` file and rejects connections to any host not already in it. This protects against man-in-the-middle attacks. Before running the tool against a new host, add it to known_hosts:

```bash
ssh-keyscan -H 192.168.1.10 >> ~/.ssh/known_hosts
# Or for a hostname:
ssh-keyscan -H workstation-01.corp.local >> ~/.ssh/known_hosts
```

To add all hosts at once from your hosts.txt:
```bash
grep -v '^#' hosts.txt | grep -v '^$' | sed 's/.*@//' | xargs -I{} ssh-keyscan -H {} >> ~/.ssh/known_hosts
```

**`TRUST_NEW_HOSTS = True` (explicit opt-in — insecure):** The script automatically accepts and saves any new host key on first connection. This disables MITM protection and should only be used on fully isolated, trusted networks where you control all machines.

---

## hosts.txt Format

One entry per line. Lines starting with `#` and blank lines are ignored.

```
# Plain IP
192.168.1.10

# Hostname
workstation-01.corp.local

# Override username for a specific host
alice@192.168.1.20
bob@workstation-02.corp.local
```

---

## Usage

### Basic run

```bash
python lock_machines.py
```

The script will:
1. Read all hosts from `hosts.txt`
2. Check whether your SSH key file exists
3. Optionally prompt for a password (used as fallback if key auth fails)
4. Connect to all machines in parallel
5. Detect the OS on each machine
6. Issue the correct silent lock command
7. Print a summary table

### Password-only (no key)

If `SSH_KEY_PATH` points to a file that doesn't exist, the script automatically switches to password-only mode and prompts you once.

### Key + password fallback

If a key is found, the script asks whether to also accept a password fallback. This is useful when some hosts accept the key but others require a password.

```
[?] SSH key found. Also prompt for password fallback? [y/N]: y
SSH password (fallback) for admin:
```

---

## Example Output

```
[*] Locking 5 machine(s) — SSH key: ~/.ssh/lock_key
[?] SSH key found. Also prompt for password fallback? [y/N]: n
[*] Connecting with up to 10 parallel workers...

  ✓ admin@192.168.1.10 — LOCKED
  ✓ admin@192.168.1.11 — LOCKED
  ✓ alice@192.168.1.12 — LOCKED
  ✗ admin@192.168.1.13 — UNREACHABLE
  ✗ admin@192.168.1.14 — FAILED

----------------------------------------------------------------------
HOST            USER   OS       STATUS       DETAIL
----------------------------------------------------------------------
192.168.1.10    admin  windows  LOCKED
192.168.1.11    admin  linux    LOCKED
192.168.1.12    alice  macos    LOCKED
192.168.1.13    admin  unknown  UNREACHABLE  [Errno 110] Connection timed out
192.168.1.14    admin  linux    FAILED       Auth failed: Authentication failed.
----------------------------------------------------------------------
Total: 5  |  Locked: 3  |  Failed: 1  |  Unreachable: 1
```

---

## Audit Log

Every time `lock_machines.py` runs it appends one entry per target machine to `lock_audit.log` (configurable via `AUDIT_LOG_FILE`). The file uses **JSON Lines** format — one self-contained JSON object per line — so it can be read by any text tool, `jq`, or imported directly into a SIEM.

### Entry format

| Field | Type | Description |
|---|---|---|
| `timestamp` | string | ISO-8601 UTC timestamp (e.g. `2026-06-11T14:30:00+00:00`) |
| `operator` | string | OS username of the person who ran the script |
| `host` | string | Target hostname or IP address |
| `os_type` | string | Detected OS: `windows`, `linux`, `macos`, or `unknown` |
| `status` | string | Outcome: `LOCKED`, `FAILED`, or `UNREACHABLE` |
| `error` | string | Error detail if status is not `LOCKED`; empty string on success |

### Example entries

```json
{"timestamp": "2026-06-11T14:30:00+00:00", "operator": "itadmin", "host": "192.168.1.10", "os_type": "windows", "status": "LOCKED", "error": ""}
{"timestamp": "2026-06-11T14:30:00+00:00", "operator": "itadmin", "host": "192.168.1.11", "os_type": "linux", "status": "LOCKED", "error": ""}
{"timestamp": "2026-06-11T14:30:00+00:00", "operator": "itadmin", "host": "192.168.1.13", "os_type": "unknown", "status": "UNREACHABLE", "error": "[Errno 110] Connection timed out"}
{"timestamp": "2026-06-11T14:30:00+00:00", "operator": "itadmin", "host": "192.168.1.14", "os_type": "linux", "status": "FAILED", "error": "Auth failed: Authentication failed."}
```

### Querying the log

```bash
# Show all failures and unreachable hosts
grep -v '"status": "LOCKED"' lock_audit.log

# Count locks per operator (requires jq)
jq -r .operator lock_audit.log | sort | uniq -c | sort -rn

# Show all entries for a specific host
grep '"host": "192.168.1.10"' lock_audit.log
```

### Log rotation

The log is rotated automatically based on the settings in `config.py`:

- **Size rotation** (`AUDIT_LOG_MAX_BYTES`): when the active log exceeds this size it is renamed to `lock_audit.log.1`, older backups shift up (`.2`, `.3`, …), and a fresh file is started. Up to `AUDIT_LOG_BACKUP_COUNT` backups are kept.
- **Age cleanup** (`AUDIT_LOG_MAX_DAYS`): each run deletes any backup files (`.1`, `.2`, …) whose last-modified time is older than this many days.

Set either value to `0` to disable that rotation strategy.

---

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `UNREACHABLE` — connection timed out | SSH port blocked or machine offline | Check firewall rules; verify SSH is running on the target |
| `UNREACHABLE` — connection refused | SSH not running on the target | Start the SSH service (see setup steps above) |
| `FAILED` — Auth failed | Wrong credentials or key not authorized | Verify `SSH_USERNAME`, key path, and `authorized_keys` on target |
| `FAILED` — Could not detect OS | SSH connected but OS probe failed | Check the user has shell access and can run `uname` or `cmd /c ver` |
| Windows: command ran but screen didn't lock | No user logged in interactively | The lock command only works when a user session is active on that machine |
| Linux: `loginctl` not found | Non-systemd distro | Install `xdg-screensaver` as a fallback (`sudo apt install xdg-screensaver`) |
| macOS: permission denied | Remote Login not enabled | Enable via System Settings → General → Sharing → Remote Login |
| `paramiko` not found | Dependency not installed | Run `pip install -r requirements.txt` |

---

## Security Notes

- **Never hardcode passwords** in `config.py` or `hosts.txt`. The script prompts at runtime.
- **SSH keys are strongly preferred** over passwords. Keys cannot be brute-forced and are not transmitted over the network.
- **Restrict the SSH user's permissions** on target machines to the minimum needed (ability to run the lock command only). Consider using `Match User` blocks in `sshd_config` to limit what the admin account can do remotely.
- This tool is intended for **authorized IT administration** within your own network. Use only on machines you own or have explicit permission to manage.
