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
# Make sure each file the container creates has the correct group set
umask 007

#######################################
############# SET LOGGING #############
#######################################
# Create log folder
mkdir -p /app/logs

#######################################
########### PYTHON START ##############
#######################################
echo "[file-ingestion] Starting file ingestion service"
exec python3 -u /app/services/ingestion/file/file_ingestion.py
