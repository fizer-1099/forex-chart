import express from 'express';
import cors from 'cors';
import webpush from 'web-push';
import fs from 'fs';

const KEY = process.env.TD_KEY, PORT = process.env.PORT || 3000, FILE = 'alerts.json';
webpush.setVapidDetails(process.env.VAPID_SUBJECT || 'mailto:you@example.com', process.env.VAPID_PUBLIC, process.env.VAPID_PRIVATE);
let alerts = fs.existsSync(FILE) ? JSON.parse(fs.readFileSync(FILE)) : [];
const save = () => fs.writeFileSync(FILE, JSON.stringify(alerts));
const q = s => /^[A-Z]{6}$/.test(s) ? s.slice(0, 3) + '/' + s.slice(3) : s;

const app = express();
app.use(cors(), express.json());
app.get('/', (_, r) => r.send('ok'));
app.get('/api/vapid', (_, r) => r.json({ key: process.env.VAPID_PUBLIC }));

// پروکسی دیتا: کلید API فقط روی سرور می‌ماند
app.get('/api/candles', async (req, res) => {
  try {
    const { symbol, interval = '15min' } = req.query;
    const u = `https://api.twelvedata.com/time_series?symbol=${encodeURIComponent(q(symbol))}&interval=${interval}&outputsize=5000&timezone=UTC&apikey=${KEY}`;
    res.json(await (await fetch(u)).json());
  } catch (e) { res.status(502).json({ message: e.message }); }
});

app.post('/api/alerts', (req, res) => {
  const { id, sub, symbol, price, ref } = req.body;
  if (!sub || !symbol || !(price > 0)) return res.status(400).end();
  alerts.push({ id, sub, symbol, price: +price, up: +price >= +ref });
  save(); res.json({ ok: 1 });
});
app.post('/api/alerts/delete', (req, res) => {
  alerts = alerts.filter(a => a.id !== req.body.id); save(); res.json({ ok: 1 });
});

// هر ۶۰ ثانیه قیمت نمادهای دارای هشدار را چک می‌کند (محدودیت پلن رایگان: ۸ درخواست در دقیقه)
setInterval(async () => {
  const syms = [...new Set(alerts.map(a => a.symbol))].slice(0, 8);
  for (const s of syms) {
    try {
      const j = await (await fetch(`https://api.twelvedata.com/price?symbol=${encodeURIComponent(q(s))}&apikey=${KEY}`)).json();
      const p = +j.price; if (!p) continue;
      for (const a of alerts.filter(a => a.symbol === s && (a.up ? p >= a.price : p <= a.price))) {
        await webpush.sendNotification(a.sub, JSON.stringify({ title: '🔔 ' + s, body: `قیمت به ${a.price} رسید (الان ${p})` })).catch(() => {});
        alerts = alerts.filter(x => x !== a);
      }
      save();
    } catch (e) {}
  }
}, 60000);

app.listen(PORT, () => console.log('server on', PORT));
