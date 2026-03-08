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
########### SUPERCRONIC START #########
#######################################
echo "[sync] Starting supercronic"
exec supercronic /tmp/sync-crontab
