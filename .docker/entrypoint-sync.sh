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
cat > /tmp/sync-crontab <<EOF
$SYNC_CRON python3 -u /app/services/dms_rag_sync/dms_rag_sync.py
EOF

#######################################
########### SUPERCRONIC START #########
#######################################
echo "[sync] Starting supercronic"
exec supercronic /tmp/sync-crontab
