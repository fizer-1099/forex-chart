@echo off
REM قبل از اجرا این دو مقدار را عوض کنید
set BRIDGE_TOKEN=PUT_A_LONG_RANDOM_TOKEN_HERE
set MT5_UTC_OFFSET=3
set MAX_LOT=0.1
python mt5_bridge.py
pause
