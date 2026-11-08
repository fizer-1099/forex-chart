@echo off
REM قبل از اجرا این دو مقدار را عوض کنید
set BRIDGE_TOKEN=PUT_A_LONG_RANDOM_TOKEN_HERE
set MT5_UTC_OFFSET=3
set MAX_LOT=0.1
REM برای تحلیل هوشمند: کلید API ی Anthropic را اینجا بگذارید (اختیاری)
set ANTHROPIC_API_KEY=
set AI_MODEL=claude-sonnet-5-5
REM ربات تلگرام (اختیاری): توکن از @BotFather و شناسه چت (برنامه با «یافتن Chat ID» نشانتان می‌دهد)
set TELEGRAM_BOT_TOKEN=
set TELEGRAM_CHAT_ID=
python mt5_bridge.py
pause
