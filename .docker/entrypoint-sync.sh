#!/bin/bash
set -e  # End script on error

#######################################
######## DEBUG PATHS AND USER #########
#######################################
echo "HOME-Verzeichnis ist auf: $HOME gesetzt"
echo "Python-Path: $PYTHONPATH"
# Show current user
USER=$(whoami)
echo "Aktueller Benutzer: $USER"
USERID=$(id -u)
echo "Benutzer-ID: $USERID"
GROUPID=$(id -g)
echo "Gruppen-ID: $GROUPID"

#######################################
########### SET PERMISSIONS ###########
#######################################
# Make sure each file the container creates has the correct group set
umask 007

#######################################
############# SET LOGGING #############
#######################################
# Create log folder
mkdir -p /app/logs

#######################################
######### GENERATE CRONTAB ###########
#######################################
# Default: every hour at minute 0
SYNC_CRON="${SYNC_CRON:-0 * * * *}"

echo "[sync] Installing crontab schedule: $SYNC_CRON"
printf '%s /app/.docker/run-sync.sh\n' "$SYNC_CRON" > /tmp/sync-crontab

#######################################
############# DIAGNOSTICS #############
#######################################
echo "[sync:diag] --- crontab contents ---"
cat /tmp/sync-crontab
echo "[sync:diag] --- file checks ---"
echo "[sync:diag] supercronic: $(which supercronic 2>/dev/null || echo 'NOT FOUND')"
echo "[sync:diag] supercronic binary exists: $(test -f /usr/local/bin/supercronic && echo YES || echo NO)"
echo "[sync:diag] supercronic executable: $(test -x /usr/local/bin/supercronic && echo YES || echo NO)"
echo "[sync:diag] run-sync.sh exists: $(test -f /app/.docker/run-sync.sh && echo YES || echo NO)"
echo "[sync:diag] run-sync.sh executable: $(test -x /app/.docker/run-sync.sh && echo YES || echo NO)"
echo "[sync:diag] /bin/sh exists: $(test -f /bin/sh && echo YES || echo NO)"
echo "[sync:diag] /bin/bash exists: $(test -f /bin/bash && echo YES || echo NO)"
echo "[sync:diag] python3 path: $(which python3 2>/dev/null || echo 'NOT FOUND')"
echo "[sync:diag] supercronic file type: $(file /usr/local/bin/supercronic 2>/dev/null || echo 'file cmd not available')"
echo "[sync:diag] uname: $(uname -m)"
echo "[sync:diag] --- end diagnostics ---"

#######################################
########### SUPERCRONIC START #########
#######################################
echo "[sync] Starting supercronic"
supercronic /tmp/sync-crontab
