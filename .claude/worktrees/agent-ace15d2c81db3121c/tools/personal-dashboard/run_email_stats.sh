#!/bin/bash
# Wrapper for gen_email_stats.py — called by launchd (com.mckay.email-stats)
# This script needs Full Disk Access for iMessage chat.db reading
cd "$(dirname "$0")"
exec /usr/bin/python3 gen_email_stats.py
