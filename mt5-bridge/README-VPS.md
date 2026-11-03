# پل MT5 روی VPS (ویندوز)

هشدار: این پل می‌تواند معامله واقعی باز کند. با **حساب دمو** شروع کنید. توکن مثل رمز عبور است و هر کس آن را داشته باشد می‌تواند معامله کند.
پیش‌فرض: حساب واقعی مسدود است، حجم حداکثر 0.1 لات و SL الزامی است.

## ۱) روی VPS
1. MT5 بروکر را نصب کنید و با **حساب دمو** وارد شوید. در Tools ← Options ← Expert Advisors گزینه **Allow algorithmic trading** را فعال کنید و دکمه AutoTrading را روشن بگذارید. MT5 باید همیشه باز بماند.
2. Python 3.10 یا بالاتر را از python.org نصب کنید (تیک Add to PATH).
3. این پوشه (`mt5-bridge`) را روی VPS بیاورید. ساده‌ترین راه: در مرورگر VPS آدرس `https://github.com/USERNAME/forex-chart/archive/refs/heads/main.zip` را باز کنید، zip را Extract کنید و وارد پوشه `mt5-bridge` شوید.
4. در PowerShell داخل همان پوشه: `pip install -r requirements.txt`
5. توکن بسازید (در PowerShell):
   `-join ((48..57)+(65..90)+(97..122) | Get-Random -Count 40 | % {[char]$_})`
6. فایل `run.bat` را با Notepad باز کنید: `BRIDGE_TOKEN` را با توکن بسازید جایگزین کنید. `MT5_UTC_OFFSET` اختلاف ساعت سرور بروکر با UTC است (مثلاً 3 یا 2؛ ساعت Market Watch را با UTC مقایسه کنید). سپس `run.bat` را اجرا کنید. باید حساب و DEMO را چاپ کند.

## ۲) HTTPS برای اپ
اپ روی https است و فقط به آدرس https وصل می‌شود. ساده‌ترین راه Cloudflare Tunnel:
1. در PowerShell دیگر: `winget install Cloudflare.cloudflared`
2. `cloudflared tunnel --url http://localhost:8787`
3. آدرسی مثل `https://xxxx.trycloudflare.com` چاپ می‌شود. این آدرس با هر راه‌اندازی دوباره عوض می‌شود.
اگر از گوشی به trycloudflare دسترسی ندارید، با یک دامنه رایگان (مثل DuckDNS) و Caddy روی پورت 443 همین را انجام دهید.

## ۳) در اپ
منو ☰ ← «اتصال MT5 (VPS)» ← آدرس https، توکن، و در صورت نیاز پسوند نماد (مثلاً `_o`) را وارد کنید ← «ذخیره و تست اتصال».

## ایمنی
- قبل از هر کاری روی دمو تست کنید. `ALLOW_REAL=1` را فقط وقتی بزنید که همه‌چیز را کامل امتحان کرده‌اید.
- توکن را در GitHub یا جای عمومی نگذارید.
- مسئولیت معامله با شماست؛ شرایط بروکر درباره معامله الگوریتمی را بررسی کنید.
