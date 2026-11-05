import express from 'express';
import cors from 'cors';
import webpush from 'web-push';
import fs from 'fs';
import { getHistoricalRates } from 'dukascopy-node';

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

// دیتای تاریخی عمیق: Dukascopy (فارکس، فلزات، شاخص‌ها) و Binance (کریپتو)، بدون نیاز به کلید
const DUKA = { EURUSD:'eurusd', GBPUSD:'gbpusd', USDCAD:'usdcad', USDCHF:'usdchf', USDJPY:'usdjpy', AUDUSD:'audusd', NZDUSD:'nzdusd',
  EURGBP:'eurgbp', EURJPY:'eurjpy', GBPJPY:'gbpjpy', AUDJPY:'audjpy', EURCHF:'eurchf', XAUUSD:'xauusd', XAGUSD:'xagusd',
  YM:'usa30idxusd', NQ:'usatechidxusd', SPX:'usa500idxusd', USOIL:'lightcmdusd' };
const BIN = { BTCUSD:'BTCUSDT', ETHUSD:'ETHUSDT' };
const BI = { m1:'1m', m5:'5m', m15:'15m', m30:'30m', h1:'1h', h4:'4h', d1:'1d' };
const cache = new Map();
async function duka(inst, tf, from, to) {
  if (tf === 's30') { // ساخت کندل ۳۰ ثانیه‌ای از دیتای تیکی
    const t = await getHistoricalRates({ instrument: inst, dates: { from, to }, timeframe: 'tick', format: 'json' });
    const m = new Map();
    for (const k of t) { const b = k.bidPrice, s = Math.floor(k.timestamp / 30000) * 30, c = m.get(s);
      if (!c) m.set(s, [s, b, b, b, b]); else { if (b > c[2]) c[2] = b; if (b < c[3]) c[3] = b; c[4] = b; } }
    return [...m.values()];
  }
  const d = await getHistoricalRates({ instrument: inst, dates: { from, to }, timeframe: tf, format: 'json', priceType: 'bid' });
  return d.map(x => [Math.floor(x.timestamp / 1000), x.open, x.high, x.low, x.close]);
}
async function binance(sym, tf, from, to) {
  const iv = BI[tf === 's30' ? 'm1' : tf] || '15m'; let t = +from, out = [];
  while (t < +to && out.length < 120000) {
    const r = await (await fetch(`https://data-api.binance.vision/api/v3/klines?symbol=${sym}&interval=${iv}&startTime=${t}&limit=1000`)).json();
    if (!Array.isArray(r) || !r.length) break;
    for (const k of r) out.push([k[0] / 1000, +k[1], +k[2], +k[3], +k[4]]);
    t = r[r.length - 1][0] + 1;
  }
  return out;
}
app.get('/api/history', async (req, res) => {
  try {
    const { symbol, tf = 'm15' } = req.query; let days = Math.min(+req.query.days || 30, 3650);
    if (tf === 's30') days = Math.min(days, 3);
    const key = `${symbol}|${tf}|${days}`, hit = cache.get(key);
    if (hit && Date.now() - hit.t < 300000) return res.json(hit.rows);
    const to = new Date(), from = new Date(Date.now() - days * 864e5);
    let rows;
    if (BIN[symbol]) rows = await binance(BIN[symbol], tf, from, to);
    else if (DUKA[symbol]) rows = await duka(DUKA[symbol], tf, from, to);
    else return res.status(404).json({ message: 'symbol not supported' });
    if (rows.length > 120000) rows = rows.slice(-120000);
    cache.set(key, { t: Date.now(), rows }); res.json(rows);
  } catch (e) { res.status(502).json({ message: e.message }); }
});

// قیمت لحظه‌ای با کش ۸ ثانیه‌ای (چند کاربر یک اعتبار مصرف می‌کنند)
const pcache = new Map();
app.get('/api/price', async (req, res) => {
  try {
    const sym = req.query.symbol, h = pcache.get(sym);
    if (h && Date.now() - h.t < 8000) return res.json(h.j);
    const j = await (await fetch(`https://api.twelvedata.com/price?symbol=${encodeURIComponent(q(sym))}&apikey=${KEY}`)).json();
    pcache.set(sym, { t: Date.now(), j }); res.json(j);
  } catch (e) { res.status(502).json({ message: e.message }); }
});

app.post('/api/alerts', (req, res) => {
  const { id, sub, symbol, price, ref, tgToken, tgChatId } = req.body;
  if (!symbol || !(price > 0)) return res.status(400).end();
  alerts.push({ id, sub, symbol, price: +price, up: +price >= +ref, tgToken, tgChatId });
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
        if (a.sub) await webpush.sendNotification(a.sub, JSON.stringify({ title: '🔔 ' + s, body: `قیمت به ${a.price} رسید (الان ${p})` })).catch(() => {});
        await sendTelegram(`🔔 <b>هشدار قیمت ${a.symbol}</b>\nقیمت به ${a.price} رسید! (قیمت فعلی: ${p})`, a.tgToken, a.tgChatId);
        alerts = alerts.filter(x => x !== a);
      }
      save();
    } catch (e) {}
  }
}, 60000);


// ===== اخبار اقتصادی (تقویم هفتگی عمومی) با کش ۳۰ دقیقه‌ای =====
let NEWS={t:0,j:null};
app.get('/api/news', async (req, res) => {
  if (NEWS.j && Date.now() - NEWS.t <= 30 * 60000) return res.json(NEWS.j);
  try {
    const r = await fetch('https://nfs.faireconomy.media/ff_calendar_thisweek.json', { headers: { 'User-Agent': 'Mozilla/5.0' } });
    if (!r.ok) throw new Error('news HTTP ' + r.status);
    NEWS = { t: Date.now(), j: await r.json() };
    res.json(NEWS.j);
  } catch (e) {
    if (NEWS.j) return res.json(NEWS.j); // اگر منبع جواب نداد، آخرین دیتای ذخیره‌شده را بده
    res.status(502).json({ error: e.message });
  }
});

// ===== تحلیل هوش مصنوعی (کلید فقط روی سرور) =====
app.post('/api/analyze', async (req, res) => {
  const KEYA = process.env.ANTHROPIC_API_KEY;
  if (!KEYA) return res.status(500).json({ error: 'ANTHROPIC_API_KEY روی سرور تنظیم نشده' });
  try {
    const { symbol = '', candles = [] } = req.body || {};
    const last = candles.slice(-150);
    const news = (NEWS.j || []).filter(e => e.impact === 'High' || e.impact === 'Medium')
      .map(e => `${e.date} | ${e.country} | ${e.impact} | ${e.title} | پیش‌بینی ${e.forecast || '-'} | قبلی ${e.previous || '-'}`).join('\n') || '(خبری در دسترس نیست)';
    const prompt = `تو یک تحلیلگر بازار مالی هستی. به فارسی، کوتاه و شفاف جواب بده و در انتها یادآوری کن که این تحلیل توصیه سرمایه‌گذاری نیست.
نماد: ${symbol}
کندل‌های اخیر (زمان یونیکس ثانیه، open، high، low، close):
${JSON.stringify(last)}
اخبار اقتصادی هفته (High و Medium):
${news}

لطفاً این سه بخش را با عنوان مشخص بنویس:
۱) تحلیل تکنیکال: روند، سطوح حمایت و مقاومت تقریبی، الگوهای مهم، و نظر کلی کوتاه‌مدت.
۲) تحلیل اقتصادی: نگاه روزانه و هفتگی بر اساس اخبار و تاثیرشان روی این نماد.
۳) جمع‌بندی: یک نتیجه کلی و سطوح کلیدی که باید رصد شوند.`;
    const r = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: { 'x-api-key': KEYA, 'anthropic-version': '2023-06-01', 'content-type': 'application/json' },
      body: JSON.stringify({ model: process.env.ANTHROPIC_MODEL || 'claude-sonnet-4-5', max_tokens: 1800, messages: [{ role: 'user', content: prompt }] })
    });
    const j = await r.json();
    if (!r.ok) throw new Error((j.error && j.error.message) || ('HTTP ' + r.status));
    res.json({ text: (j.content || []).map(c => c.text || '').join('\n') });
  } catch (e) { res.status(502).json({ error: e.message }); }
});


// ===== ربات تلگرام برای هشدارهای قیمتی =====
async function sendTelegram(text, token, chatId) {
  const tok = token || process.env.TELEGRAM_BOT_TOKEN;
  const cid = chatId || process.env.TELEGRAM_CHAT_ID;
  if (!tok || !cid) return false;
  try {
    const res = await fetch(`https://api.telegram.org/bot${tok}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: cid, text, parse_mode: 'HTML' })
    });
    return res.ok;
  } catch (e) {
    return false;
  }
}

app.post('/api/telegram/test', async (req, res) => {
  const { token, chatId } = req.body || {};
  const ok = await sendTelegram('🔔 <b>تست ربات تلگرام فارکس چارت</b>\nربات با موفقیت وصل شد و آماده ارسال هشدارهاست!', token, chatId);
  if (ok) res.json({ ok: 1 });
  else res.status(400).json({ error: 'ارسال ناموفق بود؛ توکن ربات یا Chat ID را بررسی کنید.' });
});

app.post('/api/telegram/send', async (req, res) => {
  const { text, token, chatId } = req.body || {};
  const ok = await sendTelegram(text, token, chatId);
  res.json({ ok: ok ? 1 : 0 });
});

app.listen(PORT, () => console.log('server on', PORT));
