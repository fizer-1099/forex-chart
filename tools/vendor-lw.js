// کتابخانه نمودار را از CDN به فایل محلی (lw.js) تبدیل می‌کند تا برنامه بدون اینترنت CDN هم بالا بیاید
const fs = require('fs');
const f = process.argv[2];
let s = fs.readFileSync(f, 'utf8');
const re = /<script src="https:\/\/cdnjs\.cloudflare\.com\/ajax\/libs\/lightweight-charts[^"]*"><\/script>/;
if (!re.test(s)) { console.error('script tag not found'); process.exit(1); }
s = s.replace(re, '<script src="lw.js"></script>');
fs.writeFileSync(f, s);
console.log('index.html now uses local lw.js');
