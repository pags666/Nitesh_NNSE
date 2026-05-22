@echo off
REM ============================================
REM  TRACKER AUTO-RUN — Runs after market hours
REM  Scheduled via Windows Task Scheduler
REM  Time: 4:30 PM IST daily (Mon-Fri)
REM ============================================

cd /d f:\Dev_Env\nnse

echo [%date% %time%] Starting tracker...
echo ============================================ >> tracker_log.txt
echo [%date% %time%] Run started >> tracker_log.txt

python tracker.py >> tracker_log.txt 2>&1

echo [%date% %time%] Run completed >> tracker_log.txt
echo. >> tracker_log.txt
