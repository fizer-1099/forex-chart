@echo off
REM قبل از اجرا این مقادیر را عوض کنید
set BRIDGE_TOKEN=PUT_A_LONG_RANDOM_TOKEN_HERE
REM پلتفرم: mt5 یا mt4  (برای mt4 اکسپرت mt4\FxBridge.mq4 باید داخل MT4 اجرا شود؛ راهنما در README-VPS.md)
set MT_PLATFORM=mt5
set MT5_UTC_OFFSET=3
set MAX_LOT=0.1
REM تحلیل هوشمند: ترجیحاً از داخل اپ (تحلیل هوشمند ← تنظیم سرویس) تنظیم کنید؛ اینجا فقط مقدار پیش‌فرض است
set ANTHROPIC_API_KEY=
set AI_MODEL=claude-sonnet-5-5
REM ربات تلگرام (اختیاری): توکن از @BotFather و شناسه چت (برنامه با «یافتن Chat ID» نشانتان می‌دهد)
set TELEGRAM_BOT_TOKEN=
set TELEGRAM_CHAT_ID=
python mt5_bridge.py
pause
