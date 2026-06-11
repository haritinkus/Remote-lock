#!/usr/bin/env python3
"""
lock_machines.py — Remotely lock screens on a batch of machines over SSH.
Supports Windows, Linux, and macOS targets.
"""

import os
import sys
import json
import time
import getpass
import datetime
import concurrent.futures
import logging
import logging.handlers
from dataclasses import dataclass
from typing import Optional

import paramiko

import config


STATUS_OK      = "LOCKED"
STATUS_FAIL    = "FAILED"
STATUS_UNREACH = "UNREACHABLE"


@dataclass
class LockResult:
    host: str
    user: str
    os_type: str = "unknown"
    status: str = STATUS_FAIL
    error: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_hosts(path: str) -> list[tuple[str, str]]:
    """
    Read hosts.txt and return list of (user, host) tuples.
    Lines starting with '#' or blank lines are ignored.
    Supports:
        192.168.1.10
        hostname.local
        user@192.168.1.10
    """
    entries = []
    try:
        with open(path) as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "@" in line:
                    user, host = line.split("@", 1)
                else:
                    user = config.SSH_USERNAME
                    host = line
                entries.append((user.strip(), host.strip()))
    except FileNotFoundError:
        print(f"[ERROR] Hosts file not found: {path}")
        sys.exit(1)
    return entries


def _make_client() -> paramiko.SSHClient:
    """
    Return an SSHClient with the configured host-key policy.

    TRUST_NEW_HOSTS=False (default, secure): loads the system known_hosts file
    and rejects connections to hosts not present in it.  Add unknown hosts with:
        ssh-keyscan -H <host> >> ~/.ssh/known_hosts

    TRUST_NEW_HOSTS=True (explicit opt-in, insecure): automatically accepts and
    saves new host keys — disables MITM protection.  Only use on isolated,
    fully-trusted networks.
    """
    client = paramiko.SSHClient()

    if getattr(config, "TRUST_NEW_HOSTS", False):
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())

    return client


def ssh_connect(
    host: str,
    user: str,
    key_path: Optional[str],
    password: Optional[str],
    port: int,
    timeout: int,
) -> paramiko.SSHClient:
    """
    Open an SSH connection. Tries key auth first, then password fallback.
    Raises paramiko exceptions on failure.
    """
    client = _make_client()

    expanded_key = os.path.expanduser(key_path) if key_path else None
    key_available = bool(expanded_key and os.path.isfile(expanded_key))

    try:
        if key_available:
            try:
                client.connect(
                    host,
                    port=port,
                    username=user,
                    key_filename=expanded_key,
                    timeout=timeout,
                    look_for_keys=False,
                    allow_agent=False,
                )
                return client
            except (paramiko.AuthenticationException, paramiko.SSHException):
                pass

        if password:
            client.connect(
                host,
                port=port,
                username=user,
                password=password,
                timeout=timeout,
                look_for_keys=False,
                allow_agent=False,
            )
            return client

        raise paramiko.AuthenticationException(
            "No valid authentication method available "
            "(key missing or auth failed, no password provided)."
        )
    except Exception:
        client.close()
        raise


def _run_command(
    client: paramiko.SSHClient, cmd: str, timeout: int
) -> tuple[int, str]:
    """
    Execute a command and return (exit_code, stderr_text).
    Exit code is obtained from the channel — not inferred from stderr.
    """
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    exit_code = stdout.channel.recv_exit_status()
    err_text = stderr.read().decode(errors="replace").strip()
    return exit_code, err_text


def detect_os(client: paramiko.SSHClient, timeout: int) -> str:
    """
    Detect the remote OS. Returns 'windows', 'linux', or 'macos'.
    Tries Unix-style uname first; falls back to Windows 'ver' command.
    """
    code, _ = _run_command(client, "uname -s", timeout)
    if code == 0:
        _, stdout, _ = client.exec_command("uname -s", timeout=timeout)
        output = stdout.read().decode(errors="replace").strip().lower()
        if "darwin" in output:
            return "macos"
        if "linux" in output:
            return "linux"

    code2, _ = _run_command(client, "cmd /c ver", timeout)
    if code2 == 0:
        _, stdout2, _ = client.exec_command("cmd /c ver", timeout=timeout)
        output2 = stdout2.read().decode(errors="replace").strip().lower()
        if "windows" in output2 or "microsoft" in output2:
            return "windows"

    return "unknown"


def _lock_linux(client: paramiko.SSHClient, timeout: int) -> tuple[bool, str]:
    """
    Try loginctl first; fall back to xdg-screensaver.
    Returns (success, error_detail).
    """
    code, err = _run_command(client, "loginctl lock-session", timeout)
    if code == 0:
        return True, ""

    fallback_code, fallback_err = _run_command(client, "xdg-screensaver lock", timeout)
    if fallback_code == 0:
        return True, ""

    detail = (
        f"loginctl exit={code} ({err or 'no output'}); "
        f"xdg-screensaver exit={fallback_code} ({fallback_err or 'no output'})"
    )
    return False, detail


def _lock_macos(client: paramiko.SSHClient, timeout: int) -> tuple[bool, str]:
    """
    Try CGSession -suspend first; fall back to pmset displaysleepnow.
    Returns (success, error_detail).
    """
    cgsession = (
        "/System/Library/CoreServices/Menu Extras/User.menu"
        "/Contents/Resources/CGSession -suspend"
    )
    code, err = _run_command(client, cgsession, timeout)
    if code == 0:
        return True, ""

    fallback_code, fallback_err = _run_command(client, "pmset displaysleepnow", timeout)
    if fallback_code == 0:
        return True, ""

    detail = (
        f"CGSession exit={code} ({err or 'no output'}); "
        f"pmset exit={fallback_code} ({fallback_err or 'no output'})"
    )
    return False, detail


def _lock_windows(client: paramiko.SSHClient, timeout: int) -> tuple[bool, str]:
    """
    Run rundll32 LockWorkStation.
    Note: this call is asynchronous on the remote side — it always exits 0
    if the DLL loaded. A non-zero exit indicates the command itself failed
    to launch, not that the lock was rejected.
    """
    code, err = _run_command(
        client, "rundll32.exe user32.dll,LockWorkStation", timeout
    )
    if code == 0:
        return True, ""
    return False, f"exit={code} ({err or 'no output'})"


# ---------------------------------------------------------------------------
# Core lock function
# ---------------------------------------------------------------------------

def lock_remote(
    user: str,
    host: str,
    key_path: Optional[str],
    password: Optional[str],
) -> LockResult:
    result = LockResult(host=host, user=user)

    try:
        client = ssh_connect(
            host, user, key_path, password,
            port=config.SSH_PORT,
            timeout=config.SSH_TIMEOUT,
        )
    except (paramiko.NoValidConnectionsError, OSError, TimeoutError) as e:
        result.status = STATUS_UNREACH
        result.error = str(e)
        return result
    except paramiko.AuthenticationException as e:
        result.status = STATUS_FAIL
        result.error = f"Auth failed: {e}"
        return result
    except Exception as e:
        result.status = STATUS_FAIL
        result.error = str(e)
        return result

    try:
        os_type = detect_os(client, config.SSH_TIMEOUT)
        result.os_type = os_type

        if os_type == "unknown":
            result.status = STATUS_FAIL
            result.error = "Could not detect OS"
            return result

        lock_fn = {"linux": _lock_linux, "macos": _lock_macos, "windows": _lock_windows}[os_type]
        success, detail = lock_fn(client, config.SSH_TIMEOUT)

        if success:
            result.status = STATUS_OK
        else:
            result.status = STATUS_FAIL
            result.error = detail

    except Exception as e:
        result.status = STATUS_FAIL
        result.error = str(e)
    finally:
        client.close()

    return result


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def _build_audit_logger() -> logging.Logger:
    """
    Return a Logger that writes JSON lines to AUDIT_LOG_FILE.
    Size-based rotation is applied when AUDIT_LOG_MAX_BYTES > 0.
    The logger is cached on the module so repeated calls return the same one.
    """
    logger = logging.getLogger("lock_audit")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    log_path = getattr(config, "AUDIT_LOG_FILE", "lock_audit.log")
    max_bytes = getattr(config, "AUDIT_LOG_MAX_BYTES", 0)
    backup_count = getattr(config, "AUDIT_LOG_BACKUP_COUNT", 7)

    if max_bytes and max_bytes > 0:
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
    else:
        handler = logging.FileHandler(log_path, encoding="utf-8")

    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def _purge_old_audit_backups() -> None:
    """
    Delete rotated backup files (lock_audit.log.1, .2, …) that are older
    than AUDIT_LOG_MAX_DAYS days.  Silently skips if the setting is 0 or
    the log file has no backup siblings.
    """
    max_days = getattr(config, "AUDIT_LOG_MAX_DAYS", 0)
    if not max_days:
        return

    log_path = getattr(config, "AUDIT_LOG_FILE", "lock_audit.log")
    cutoff = time.time() - max_days * 86400
    backup_count = getattr(config, "AUDIT_LOG_BACKUP_COUNT", 7)

    for i in range(1, backup_count + 1):
        candidate = f"{log_path}.{i}"
        try:
            if os.path.isfile(candidate) and os.path.getmtime(candidate) < cutoff:
                os.remove(candidate)
        except OSError:
            pass


def write_audit_log(results: list[LockResult], operator: str) -> None:
    """
    Append one JSON-Lines entry per LockResult to the audit log file.

    Each entry contains:
        timestamp  — ISO-8601 UTC timestamp of when the entry was written
        operator   — local username running the script
        host       — target hostname / IP
        os_type    — detected OS ('windows', 'linux', 'macos', 'unknown')
        status     — LOCKED | FAILED | UNREACHABLE
        error      — error detail string (empty string on success)
    """
    logger = _build_audit_logger()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    for result in results:
        entry = {
            "timestamp": now,
            "operator": operator,
            "host": result.host,
            "os_type": result.os_type,
            "status": result.status,
            "error": result.error,
        }
        logger.info(json.dumps(entry, ensure_ascii=False))

    _purge_old_audit_backups()


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_summary(results: list[LockResult]) -> None:
    col_host   = max((len(r.host)    for r in results), default=10)
    col_user   = max((len(r.user)    for r in results), default=8)
    col_os     = max((len(r.os_type) for r in results), default=7)
    col_status = max((len(r.status)  for r in results), default=10)

    col_host   = max(col_host,   4)
    col_user   = max(col_user,   4)
    col_os     = max(col_os,     2)
    col_status = max(col_status, 6)

    header = (
        f"{'HOST':<{col_host}}  "
        f"{'USER':<{col_user}}  "
        f"{'OS':<{col_os}}  "
        f"{'STATUS':<{col_status}}  "
        f"DETAIL"
    )
    divider = "-" * (len(header) + 20)

    print()
    print(divider)
    print(header)
    print(divider)

    locked = failed = unreachable = 0

    for r in results:
        print(
            f"{r.host:<{col_host}}  "
            f"{r.user:<{col_user}}  "
            f"{r.os_type:<{col_os}}  "
            f"{r.status:<{col_status}}  "
            f"{r.error}"
        )
        if r.status == STATUS_OK:
            locked += 1
        elif r.status == STATUS_UNREACH:
            unreachable += 1
        else:
            failed += 1

    print(divider)
    print(
        f"Total: {len(results)}  |  "
        f"Locked: {locked}  |  "
        f"Failed: {failed}  |  "
        f"Unreachable: {unreachable}"
    )
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    hosts = parse_hosts(config.HOSTS_FILE)

    if not hosts:
        print("[WARNING] No hosts found in hosts.txt. Add entries and try again.")
        sys.exit(0)

    trust = getattr(config, "TRUST_NEW_HOSTS", False)
    key_path = config.SSH_KEY_PATH
    expanded_key = os.path.expanduser(key_path) if key_path else None
    key_available = bool(expanded_key and os.path.isfile(expanded_key))

    print(f"[*] Locking {len(hosts)} machine(s) — SSH key: {key_path}")
    if trust:
        print("[!] WARNING: TRUST_NEW_HOSTS=True — host key verification is disabled.")
    else:
        print("[*] Host key verification ON (TRUST_NEW_HOSTS=False). Unknown hosts will be rejected.")

    password: Optional[str] = None
    if not key_available:
        print(f"[!] SSH key not found at {key_path}. Falling back to password auth.")
        password = getpass.getpass(f"SSH password for {config.SSH_USERNAME}: ")
    else:
        use_pw = input("[?] SSH key found. Also prompt for password fallback? [y/N]: ").strip().lower()
        if use_pw == "y":
            password = getpass.getpass(f"SSH password (fallback) for {config.SSH_USERNAME}: ")

    print(f"[*] Connecting with up to {config.MAX_WORKERS} parallel workers...\n")

    results: list[LockResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
        futures = {
            executor.submit(lock_remote, user, host, key_path, password): (user, host)
            for user, host in hosts
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            icon = "✓" if result.status == STATUS_OK else "✗"
            print(f"  {icon} {result.user}@{result.host} — {result.status}")
            results.append(result)

    results.sort(key=lambda r: (r.status != STATUS_OK, r.host))
    print_summary(results)

    operator = getpass.getuser()
    write_audit_log(results, operator)
    print(f"[*] Audit log updated: {config.AUDIT_LOG_FILE}")


if __name__ == "__main__":
    main()
