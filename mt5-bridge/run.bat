@echo off
REM قبل از اجرا این مقادیر را عوض کنید
set BRIDGE_TOKEN=PUT_A_LONG_RANDOM_TOKEN_HERE
REM پلتفرم: both (هر دو همزمان؛ انتخاب از داخل اپ) یا mt5 یا mt4
REM برای MT4 اکسپرت mt4\FxBridge.mq4 باید داخل MT4 اجرا شود؛ راهنما در README-VPS.md
set MT_PLATFORM=both
set MT5_UTC_OFFSET=3
set MAX_LOT=0.1
REM پورت اکسپرت MT4 (فقط داخل خود VPS). MT4 فقط پورت 80 یا 443 را قبول می‌کند
set EA_PORT=80
REM تحلیل هوشمند: ترجیحاً از داخل اپ (تحلیل هوشمند ← تنظیم سرویس) تنظیم کنید؛ اینجا فقط مقدار پیش‌فرض است
set ANTHROPIC_API_KEY=
set AI_MODEL=claude-sonnet-5-5
REM ربات تلگرام (اختیاری): توکن از @BotFather و شناسه چت (برنامه با «یافتن Chat ID» نشانتان می‌دهد)
set TELEGRAM_BOT_TOKEN=
set TELEGRAM_CHAT_ID=
REM اگر تست تلگرام خطای CERTIFICATE_VERIFY_FAILED داد، این خط را از حالت کامنت دربیاورید (0 = بدون بررسی گواهی، فقط برای تلگرام)
REM set TELEGRAM_VERIFY_SSL=0
python mt5_bridge.py
pause
