SSH_USERNAME = "admin"

SSH_KEY_PATH = "~/.ssh/id_rsa"

SSH_PORT = 22

SSH_TIMEOUT = 10

MAX_WORKERS = 10

HOSTS_FILE = "hosts.txt"

# Host key verification policy.
# False (default) — verify host keys against ~/.ssh/known_hosts.
#   Connections to unknown hosts will be REJECTED. Add target hosts to
#   known_hosts first: ssh-keyscan -H <host> >> ~/.ssh/known_hosts
# True — automatically trust and save new host keys on first connection.
#   WARNING: this disables MITM protection. Only use on isolated,
#   trusted networks where you control all hosts.
TRUST_NEW_HOSTS = False

# ---------------------------------------------------------------------------
# Audit log settings
# ---------------------------------------------------------------------------

# Path to the audit log file. Each lock attempt is appended as a JSON line.
AUDIT_LOG_FILE = "lock_audit.log"

# Maximum size of the log file in bytes before it is rotated.
# When the file exceeds this size it is renamed to lock_audit.log.1
# (up to AUDIT_LOG_BACKUP_COUNT backups are kept).
# Set to 0 to disable size-based rotation.
AUDIT_LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB

# Number of rotated backup files to keep alongside the active log.
# Oldest backups beyond this count are deleted automatically.
# Only used when AUDIT_LOG_MAX_BYTES > 0.
AUDIT_LOG_BACKUP_COUNT = 7

# Delete rotated backup files older than this many days.
# Set to 0 to disable age-based cleanup of backup files.
AUDIT_LOG_MAX_DAYS = 90
