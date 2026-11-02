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
