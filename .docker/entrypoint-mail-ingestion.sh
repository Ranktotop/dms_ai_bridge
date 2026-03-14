#!/bin/bash
set -e  # End script on error

#######################################
######## DEBUG PATHS AND USER #########
#######################################
echo "HOME-Verzeichnis ist auf: $HOME gesetzt"
echo "Python-Path: $PYTHONPATH"
USER=$(whoami)
echo "Aktueller Benutzer: $USER"
USERID=$(id -u)
echo "Benutzer-ID: $USERID"
GROUPID=$(id -g)
echo "Gruppen-ID: $GROUPID"

#######################################
########### SET PERMISSIONS ###########
#######################################
umask 007

#######################################
############# SET LOGGING #############
#######################################
mkdir -p /app/logs

#######################################
########### PYTHON START ##############
#######################################
echo "[mail-ingestion] Starting mail ingestion service"
exec python3 -u /app/services/ingestion/mail/mail_ingestion.py
