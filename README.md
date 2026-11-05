# Forex Chart

## ۱) فرستادن روی GitHub با Termux
```
pkg update && pkg install git gh unzip
termux-setup-storage
cd ~ && unzip ~/storage/downloads/forex-chart-app.zip && cd forex-chart-app
git init -b main && git add . && git commit -m "init"
gh auth login
gh repo create forex-chart --public --source=. --push
```
در GitHub: Settings ← Pages ← Source = **GitHub Actions**. بعد از چند دقیقه آدرس `https://USERNAME.github.io/forex-chart/` فعال می‌شود.

## ۲) نصب روی اندروید (PWA، پیشنهادی)
آدرس را در Chrome باز کنید ← منوی سه‌نقطه ← **Install app** (افزودن به صفحه اصلی). مثل یک اپ مستقل باز می‌شود و اعلان پس‌زمینه هم کار می‌کند.

## ۳) سرور (دیتا و هشدار پس‌زمینه)
۱. کلید رایگان از twelvedata.com بگیرید.
۲. کلیدهای اعلان: `cd server && npm install && npx web-push generate-vapid-keys`
۳. روی Render.com (یا Railway/Fly): New Web Service از همین ریپو، Root Directory = `server`، Build = `npm install`، Start = `node index.js`، و متغیرها:
   `TD_KEY`، `VAPID_PUBLIC`، `VAPID_PRIVATE`، `VAPID_SUBJECT` (مثلاً mailto:you@gmail.com)
۴. آدرس سرویس را در `app/config.js` بگذارید و `git add . && git commit -m srv && git push`.
۵. در اپ: پنل «هشدار» ← «اعلان پس‌زمینه» ← اجازه اعلان را بدهید.

نکته: دیسک پلن رایگان Render موقتی است و هشدارهای ذخیره‌شده با ری‌استارت سرویس پاک می‌شوند.

## ۴) فایل APK (اختیاری)
GitHub ← Actions ← **Android APK** ← Run workflow. بعد از چند دقیقه فایل `app-debug.apk` را از Artifacts دانلود و نصب کنید.
توجه: نسخه APK اعلان پس‌زمینه (Push) ندارد، چون به Firebase نیاز دارد. برای هشدار پس‌زمینه از نسخه PWA استفاده کنید.

## ۵) دیتای عمیق‌تر
با سرور فعال، دکمه «دیتای زنده API» تایم‌فریم پایه (s30، m1، m5، m15، m30، h1، h4، d1) و تعداد روز را می‌پرسد.
- فارکس، طلا، نقره، شاخص‌ها و نفت: از Dukascopy (چندین سال، بدون کلید). `s30` از دیتای تیکی ساخته می‌شود و حداکثر ۳ روز است.
- BTCUSD و ETHUSD: از Binance (حتی بدون سرور).
- اگر منبع بالا جواب ندهد، Twelve Data (حداکثر ۵۰۰۰ کندل) استفاده می‌شود.
بعد از تغییر سرور: `git push` کافی است، Render خودکار دوباره می‌سازد.


## ۶) تحلیل هوش مصنوعی
روی Render در Environment یک متغیر `ANTHROPIC_API_KEY` با کلید API خودتان اضافه کنید. (اختیاری: `ANTHROPIC_MODEL` برای انتخاب مدل.) بدون این کلید بقیه برنامه کار می‌کند، فقط دکمه تحلیل خطا می‌دهد.


## ۷) محافظ اخبار (News Guard)
اپ به صورت خودکار تقویم اخبار اقتصادی را بررسی می‌کند و در صورت نزدیک شدن به اخبار با تاثیر بالا (High Impact) برای ارزهای نماد جاری:
- یک نوار هشدار در بالای چارت با شمارش معکوس به نمایش درمی‌آید.
- از منوی «تنظیمات چارت» می‌توانید گزینه «بستن خودکار معامله قبل از خبر مهم» را فعال کنید تا معاملات باز روی MT5 به شکل خودکار قبل از زمان خبر بسته شوند و از ضررهای شدید ناشی از نوسان اخبار جلوگیری شود.

## ۸) ربات تلگرام برای هشدارهای قیمت
برای دریافت هشدارهای قیمتی مستقیم روی گوشی در تلگرام:
۱. در تلگرام به @BotFather پیام دهید و با دستور `/newbot` یک ربات بسازید و توکن آن را کپی کنید.
۲. با پیام به @userinfobot، شناسه کاربری (Chat ID) عددی خود را بگیرید.
۳. در اپ، منو ☰ ← «ربات تلگرام (هشدارها)» را باز کرده و توکن و Chat ID را وارد کنید و دکمه تست را بزنید.
(می‌توانید `TELEGRAM_BOT_TOKEN` و `TELEGRAM_CHAT_ID` را روی متغیرهای محیطی سرور Render نیز تعریف کنید تا هشدارهای پس‌زمینه سرور هم مستقیماً به تلگرام ارسال شوند).
