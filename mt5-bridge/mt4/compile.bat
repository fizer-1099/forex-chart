@echo off
chcp 65001 >nul
REM 1) مسیر MetaEditor مربوط به MT4 را اینجا بدهید:
set ME="C:\Program Files (x86)\MetaTrader 4\metaeditor.exe"
REM 2) پوشه داده MT4 (در MT4: File ← Open Data Folder) را بدون اسلش آخر اینجا بدهید:
set DATA=C:\Users\%USERNAME%\AppData\Roaming\MetaQuotes\Terminal\PUT_TERMINAL_ID_HERE

copy /Y "%~dp0FxBridge.mq4" "%DATA%\MQL4\Experts\FxBridge.mq4"
%ME% /compile:"%DATA%\MQL4\Experts\FxBridge.mq4" /log
echo.
if exist "%DATA%\MQL4\Experts\FxBridge.log" (type "%DATA%\MQL4\Experts\FxBridge.log") else echo لاگ پیدا نشد؛ مسیرها را بررسی کنید
if exist "%DATA%\MQL4\Experts\FxBridge.ex4" (echo OK: FxBridge.ex4 ساخته شد - MT4 را ریستارت یا Navigator را Refresh کنید) else echo ex4 ساخته نشد - دستی با F7 در MetaEditor کامپایل کنید
pause
