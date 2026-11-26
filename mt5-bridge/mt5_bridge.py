"""پل بین اپ و MetaTrader (MT5 یا MT4) روی VPS ویندوزی.
- فقط روی 127.0.0.1 گوش می‌دهد؛ با Cloudflare Tunnel (HTTPS) در دسترس اپ قرار می‌گیرد.
- همه درخواست‌ها نیاز به هدر X-Token دارند.
- پیش‌فرض: فقط حساب دمو. حساب واقعی فقط با ALLOW_REAL=1.
- پلتفرم با متغیر MT_PLATFORM انتخاب می‌شود: mt5 (پیش‌فرض) یا mt4 (با اکسپرت FxBridge.mq4).
"""
import os, time, hmac, threading, json as _json, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from flask import Flask, request, jsonify, has_request_context
from flask_cors import CORS
from backends import BErr, make_backend

TOKEN = os.environ.get("BRIDGE_TOKEN", "")
MAX_LOT = float(os.environ.get("MAX_LOT", "0.1"))
ALLOW_REAL = os.environ.get("ALLOW_REAL", "0") == "1"
UTC_OFFSET_H = os.environ.get("MT5_UTC_OFFSET")  # اختلاف ساعت سرور بروکر با UTC (ساعت)
PORT = int(os.environ.get("BRIDGE_PORT", "8787"))
EA_PORT = int(os.environ.get("EA_PORT", "80"))  # پورت مخصوص اکسپرت MT4 (MT4 فقط پورت 80/443 را در WebRequest می‌پذیرد)
PLATFORM = os.environ.get("MT_PLATFORM", "both").strip().lower()  # mt5 | mt4 | both
if len(TOKEN) < 24:
    raise SystemExit("BRIDGE_TOKEN باید حداقل ۲۴ کاراکتر باشد")

# هر دو پلتفرم می‌توانند همزمان فعال باشند؛ اپ با هدر X-Platform یکی را انتخاب می‌کند
BACKENDS = {}
for _k in (["mt5", "mt4"] if PLATFORM in ("both", "auto", "") else [PLATFORM]):
    try:
        BACKENDS[_k] = make_backend(_k, UTC_OFFSET_H)
    except Exception as _e:
        print(f"پلتفرم {_k} فعال نشد: {_e}")
if not BACKENDS:
    raise SystemExit("هیچ پلتفرمی فعال نشد (MT_PLATFORM را mt4 یا mt5 بگذارید)")
DEFAULT = BACKENDS.get("mt5") or BACKENDS["mt4"]
_tl = threading.local()


def _cur():
    b = getattr(_tl, "be", None)
    if b is not None:
        return b
    if has_request_context():
        k = (request.headers.get("X-Platform") or request.args.get("platform") or "").strip().lower()
        if k:
            if k not in BACKENDS:
                raise BErr(f"پلتفرم {k.upper()} روی پل فعال نیست (MT_PLATFORM در run.bat را بررسی کنید)", 400)
            return BACKENDS[k]
    return DEFAULT


class _Proxy:
    def __getattr__(self, n):
        return getattr(_cur(), n)


BE = _Proxy()

app = Flask(__name__)
CORS(app, allow_headers=["X-Token", "Content-Type", "X-Platform"])


@app.errorhandler(BErr)
def _berr(e):
    return jsonify(error=e.msg), e.code


@app.after_request
def _gzip(r):
    """پاسخ‌های JSON بزرگ (کندل‌ها) فشرده می‌شوند تا بارگذاری سریع‌تر شود."""
    try:
        if (r.status_code != 200 or r.direct_passthrough or r.mimetype != "application/json" or "Content-Encoding" in r.headers
                or "gzip" not in request.headers.get("Accept-Encoding", "")):
            return r
        data = r.get_data()
        if len(data) < 4096:
            return r
        import gzip
        z = gzip.compress(data, compresslevel=3)
        r.set_data(z)
        r.headers["Content-Encoding"] = "gzip"
        r.headers["Content-Length"] = str(len(z))
        r.headers["Vary"] = "Accept-Encoding"
    except Exception:
        pass
    return r


@app.before_request
def auth():
    if request.method == "OPTIONS":
        return None
    if not hmac.compare_digest(request.headers.get("X-Token", ""), TOKEN):
        return jsonify(error="unauthorized"), 401


def guard():
    a = BE.account()
    if a is None:
        return f"{BE.name} آماده نیست"
    if not a["algo"]:
        return BE.algo_hint
    if not a["demo"] and not ALLOW_REAL:
        return "حساب واقعی مسدود است (ALLOW_REAL=1 لازم است)"
    return None


# ---- مسیرهای اختصاصی اکسپرت MT4 ----
@app.post("/ea/poll")
def ea_poll():
    b = BACKENDS.get("mt4")
    if b is None:
        return jsonify(error="MT4 روی پل فعال نیست (MT_PLATFORM)"), 400
    d = _json.loads(request.get_data().decode("utf-8", "replace").replace("\x00", "") or "{}")
    return b.ea_poll(d), 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.post("/ea/result")
def ea_result():
    b = BACKENDS.get("mt4")
    if b is None:
        return jsonify(error="MT4 روی پل فعال نیست (MT_PLATFORM)"), 400
    b.ea_result(request.args["id"], request.args.get("last", "1") == "1", request.get_data().decode("utf-8", "replace"))
    return "ok"


@app.get("/api/platforms")
def platforms():
    out = {}
    for k, b in BACKENDS.items():
        try:
            a = b.account()
        except Exception:
            a = None
        out[k] = dict(online=a is not None, login=a["login"] if a else None, server=a["server"] if a else None,
                      demo=a["demo"] if a else None)
    return jsonify(platforms=out, default=DEFAULT.name.lower())


@app.get("/api/ping")
def ping():
    a = BE.account()
    if a is None:
        return jsonify(error=f"{BE.name} آماده نیست" + (" (اکسپرت FxBridge در MT4 وصل نیست)" if BE.name == "MT4" else "")), 503
    return jsonify(login=a["login"], server=a["server"], demo=a["demo"], balance=a["balance"], equity=a["equity"],
                   currency=a["currency"], allow_real=ALLOW_REAL, max_lot=MAX_LOT, algo=a["algo"],
                   ai=bool(ai_conf()["key"]), tg=bool(all(tg_conf())), platform=BE.name)


@app.get("/api/candles")
def candles():
    sym = request.args["symbol"]
    n = min(int(request.args.get("n", "5000")), 50000)
    d, rows = BE.candles(sym, request.args.get("tf", "m15"), n)
    return jsonify(digits=d, rows=rows)


@app.get("/api/tick")
def tick():
    t = BE.tick(request.args["symbol"])
    if t is None:
        return jsonify(error="no tick"), 404
    return jsonify(t)


@app.get("/api/positions")
def positions():
    ps, os_ = BE.positions()
    a = BE.account()
    if a is None:
        return jsonify(error=f"{BE.name} آماده نیست"), 503
    pf = BE.name.lower()
    return jsonify(positions=ps, orders=os_, balance=a["balance"], equity=a["equity"], profit=a["profit"],
                   free=a["free"], margin=a["margin"], level=a["level"],
                   trails=[dict(ticket=t["ticket"], dist=t["dist"]) for t in TRAILS if t["pf"] == pf])


@app.post("/api/order")
def order():
    g = guard()
    if g:
        return jsonify(error=g), 403
    d = request.get_json(force=True)
    sym, side, typ = d["symbol"], d["side"], d.get("type", "mkt")
    lots, sl, tp = float(d["lots"]), float(d.get("sl") or 0), float(d.get("tp") or 0)
    info = BE.symbol_info(sym)
    if info is None:
        return jsonify(error="نماد پیدا نشد"), 404
    if sl <= 0:
        return jsonify(error="حد ضرر (SL) الزامی است"), 400
    if lots > MAX_LOT:
        return jsonify(error=f"حجم بیشتر از سقف MAX_LOT={MAX_LOT} است"), 400
    step = info["volume_step"]
    lots = round(round(lots / step) * step, 2)
    if lots < info["volume_min"] or lots > info["volume_max"]:
        return jsonify(error="حجم نامعتبر"), 400
    t = BE.tick(sym)
    if t is None:
        return jsonify(error="قیمت نماد در دسترس نیست"), 503
    if typ != "mkt":
        if typ not in ("bl", "sl", "bs", "ss"):
            return jsonify(error="نوع سفارش نامعتبر"), 400
        price = float(d["price"])
    else:
        price = t["ask"] if side == "B" else t["bid"]
    if (side == "B" and sl >= price) or (side == "S" and sl <= price):
        return jsonify(error="SL در سمت اشتباه است"), 400
    why = _rules_check(BE, sym, lots, abs(price - sl), info, manual=True)
    if why:
        return jsonify(error="قوانین ریسک: " + why), 403
    if tp and ((side == "B" and tp <= price) or (side == "S" and tp >= price)):
        return jsonify(error="TP در سمت اشتباه است"), 400
    return jsonify(BE.order(sym, side, typ, lots, price, sl, tp))


@app.post("/api/modify")
def modify():
    g = guard()
    if g:
        return jsonify(error=g), 403
    d = request.get_json(force=True)
    sl, tp = float(d.get("sl") or 0), float(d.get("tp") or 0)
    if sl <= 0:
        return jsonify(error="حد ضرر (SL) الزامی است"), 400
    return jsonify(BE.modify(int(d["ticket"]), sl, tp))


@app.post("/api/close")
def close():
    g = guard()
    if g:
        return jsonify(error=g), 403
    d = request.get_json(force=True)
    k = (BE.name.lower(), int(d["ticket"]))
    MANUAL[k] = time.time()  # بستن دستی از اپ؛ در پیام تلگرام «دستی» نوشته می‌شود
    try:
        return jsonify(BE.close(int(d["ticket"]), d.get("volume")))
    except Exception:
        MANUAL.pop(k, None)
        raise


@app.get("/api/symbols")
def symbols():
    # نمادهای روشن در Market Watch
    return jsonify(BE.symbols(request.args.get("all") == "1"))


@app.get("/api/symbol")
def symbol():
    i = BE.symbol_info(request.args["symbol"])
    if i is None:
        return jsonify(error="نماد پیدا نشد"), 404
    return jsonify(tick_value=i["tick_value"], tick_size=i["tick_size"], digits=i["digits"],
                   spread=i["spread"], volume_min=i["volume_min"], volume_step=i["volume_step"],
                   volume_max=i["volume_max"])


@app.get("/api/watch")
def watch():
    return jsonify(BE.watch([s for s in request.args.get("symbols", "").split(",") if s]))


@app.get("/api/history")
def history():
    return jsonify(BE.history(int(request.args["from"]), int(request.args["to"])))


@app.get("/api/margin")
def margin():
    sym, side, lots = request.args["symbol"], request.args.get("side", "B"), float(request.args["lots"])
    m, free = BE.margin(sym, side, lots)
    return jsonify(margin=m, free=free)


@app.post("/api/modifyorder")
def modifyorder():
    g = guard()
    if g:
        return jsonify(error=g), 403
    d = request.get_json(force=True)
    sl = float(d.get("sl") or 0)
    if sl <= 0:
        return jsonify(error="حد ضرر (SL) الزامی است"), 400
    return jsonify(BE.modify_order(int(d["ticket"]), float(d["price"]), sl, float(d.get("tp") or 0)))


@app.post("/api/cancel")
def cancel():
    g = guard()
    if g:
        return jsonify(error=g), 403
    return jsonify(BE.cancel(int(request.get_json(force=True)["ticket"])))


# ===================== اخبار، تقویم اقتصادی و تحلیل هوشمند =====================
CACHE = {}


def cached(key, ttl, fn):
    h = CACHE.get(key)
    if h and time.time() - h[0] < ttl:
        return h[1]
    try:
        v = fn()
    except Exception:
        if h:  # اگر دریافت جدید شکست خورد، نسخه قبلی را بده
            return h[1]
        raise
    CACHE[key] = (time.time(), v)
    return v


def _ssl_ctx():
    """TELEGRAM_VERIFY_SSL=0 فقط برای تلگرام: وقتی VPS/آنتی‌ویروس/پروکسی گواهی خودامضا وسط راه می‌گذارد."""
    import ssl
    if os.environ.get("TELEGRAM_VERIFY_SSL", "1") == "0":
        return ssl._create_unverified_context()
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def http_get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (fxapp)"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx() if "api.telegram.org" in url else None) as r:
        return r.read()


def load_calendar():
    raw = _json.loads(http_get("https://nfs.faireconomy.media/ff_calendar_thisweek.json"))
    out = []
    for e in raw:
        try:
            ts = int(datetime.fromisoformat(e["date"].replace("Z", "+00:00")).timestamp())
        except Exception:
            continue
        out.append(dict(title=e.get("title", ""), country=e.get("country", ""), ts=ts, impact=e.get("impact", ""),
                        forecast=e.get("forecast", ""), previous=e.get("previous", "")))
    return out


FEEDS = ["https://www.fxstreet.com/rss/news", "https://www.forexlive.com/feed/news"]


def load_headlines():
    out = []
    for u in FEEDS:
        try:
            root = ET.fromstring(http_get(u))
            for it in root.iter("item"):
                try:
                    ts = int(parsedate_to_datetime(it.findtext("pubDate") or "").timestamp())
                except Exception:
                    ts = 0
                out.append(dict(title=(it.findtext("title") or "").strip(), link=(it.findtext("link") or "").strip(),
                                ts=ts, src=u.split("/")[2]))
        except Exception:
            continue
    out.sort(key=lambda x: -x["ts"])
    if not out:
        raise RuntimeError("هیچ خبری دریافت نشد")
    return out[:40]


@app.get("/api/calendar")
def calendar():
    try:
        return jsonify(cached("cal", 1800, load_calendar))
    except Exception as ex:
        return jsonify(error=f"دریافت تقویم ناموفق بود: {ex}"), 502


@app.get("/api/headlines")
def headlines():
    try:
        return jsonify(cached("hl", 600, load_headlines))
    except Exception as ex:
        return jsonify(error=f"دریافت اخبار ناموفق بود: {ex}"), 502


def _ema(v, n):
    k, e = 2 / (n + 1), v[0]
    for x in v[1:]:
        e = x * k + e * (1 - k)
    return e


def _rsi(c, n=14):
    g = l = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        g += max(d, 0)
        l += max(-d, 0)
    g, l = g / n, l / n
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n
        l = (l * (n - 1) + max(-d, 0)) / n
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


def _atr(h, l, c, n=14):
    trs = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    a = sum(trs[:n]) / n
    for x in trs[n:]:
        a = (a * (n - 1) + x) / n
    return a


def snapshot(sym):
    d = 5
    res = {}
    for name, tf, n in (("D1", "d1", 260), ("H4", "h4", 200), ("H1", "h1", 120)):
        try:
            d, rows = BE.candles(sym, tf, n)
        except BErr:
            continue
        if len(rows) < 40:
            continue
        c = [r[4] for r in rows]
        h = [r[2] for r in rows]
        lo = [r[3] for r in rows]
        res[name] = dict(
            last=round(c[-1], d), chg_pct_1bar=round((c[-1] / c[-2] - 1) * 100, 3),
            chg_pct_5bar=round((c[-1] / c[-6] - 1) * 100, 3), ema20=round(_ema(c, 20), d), ema50=round(_ema(c, 50), d),
            ema200=round(_ema(c, 200), d) if len(c) >= 200 else None, rsi14=round(_rsi(c), 1),
            atr14=round(_atr(h, lo, c), d), high20=round(max(h[-20:]), d), low20=round(min(lo[-20:]), d),
            high60=round(max(h[-60:]), d), low60=round(min(lo[-60:]), d))
    return res


def ccy_of(sym):
    s = "".join(ch for ch in sym if ch.isalpha())[:6].upper()
    return {s[:3], s[3:6]} if len(s) == 6 else {"USD"}


def iran(ts):
    return (datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=3, minutes=30)).strftime("%Y-%m-%d %H:%M")


def call_claude(prompt):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY روی VPS تنظیم نشده است (فایل run.bat)")
    body = _json.dumps({"model": os.environ.get("AI_MODEL", "claude-sonnet-5-5"), "max_tokens": 4500,
                        "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=170) as r:
            j = _json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"خطای سرویس هوش مصنوعی {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}")
    return "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")


@app.post("/api/analyze")
def analyze():
    d = request.get_json(force=True)
    symbols = [s for s in d.get("symbols", []) if isinstance(s, str)][:6]
    scope = d.get("scope", "both")
    if not symbols:
        return jsonify(error="نمادی انتخاب نشده"), 400

    def build():
        now = int(time.time())
        cur = set()
        for s in symbols:
            cur |= ccy_of(s)
        try:
            cal = cached("cal", 1800, load_calendar)
        except Exception:
            cal = []
        evs = [e for e in cal if e["impact"] in ("High", "Medium") and e["country"] in (cur | {"USD"})]
        if scope == "daily":
            evs = [e for e in evs if now - 6 * 3600 <= e["ts"] <= now + 36 * 3600]
        evs = sorted(evs, key=lambda e: e["ts"])[:70]
        try:
            news = [h["title"] for h in cached("hl", 600, load_headlines)][:25]
        except Exception:
            news = []
        data = dict(now_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"), now_iran=iran(now),
                    symbols={s: snapshot(s) for s in symbols},
                    calendar=[dict(time_iran=iran(e["ts"]), ccy=e["country"], impact=e["impact"], event=e["title"],
                                   forecast=e["forecast"], previous=e["previous"]) for e in evs],
                    headlines=news)
        want = {"daily": "تحلیل روزانه", "weekly": "تحلیل هفتگی", "both": "تحلیل روزانه و هفتگی"}[scope]
        prompt = f"""تو یک تحلیل‌گر ارشد بازارهای مالی (فارکس، فلزات، شاخص‌ها و کریپتو) هستی. بر اساس فقط داده‌های زیر (JSON) یک گزارش جامع و منظم به فارسی بنویس. نوع گزارش: {want}.

قواعد:
- فقط از داده‌های داده‌شده استفاده کن و چیزی از خودت درباره قیمت‌ها یا رویدادهایی که در داده نیست نساز. اگر داده‌ای ناقص بود صریح بگو.
- ساعت‌ها را به وقت ایران بنویس.
- قطعی‌گویی نکن؛ سناریوهای صعودی و نزولی، سطوح کلیدی (حمایت و مقاومت از high/low و EMAها) و شرایط ابطال را بگو.
- قالب: عناوین با ## ، فهرست با - ، متن‌های مهم با **bold**.

بخش‌های گزارش:
## خلاصه کلی بازار
## تحلیل اقتصادی (روزانه و هفتگی طبق نوع گزارش): رویدادهای مهم تقویم، ارزهای درگیر، زمان انتشار و اثر احتمالی
## تحلیل اخبار: جمع‌بندی سرفصل‌ها و موضوعات مسلط بازار
## تحلیل تکنیکال هر نماد: روند در D1 و H4، RSI، ATR، سطوح کلیدی، سناریوها
## ریسک‌ها و نکات مدیریت سرمایه
## جمع‌بندی نهایی (در چند خط)
در پایان یک جمله بنویس که این تحلیل آموزشی است و توصیه مالی نیست.

داده‌ها:
{_json.dumps(data, ensure_ascii=False)}"""
        return call_llm(prompt)

    try:
        text = cached("ai|" + BE.name + "|" + "|".join(sorted(symbols)) + "|" + scope, 1200, build)
        return jsonify(text=text)
    except Exception as ex:
        return jsonify(error=str(ex)), 502


# ===================== تلگرام، هشدار قیمت سمت سرور و محافظ اخبار =====================
BASE = os.path.dirname(os.path.abspath(__file__))


def _load(name, default):
    try:
        with open(os.path.join(BASE, name), encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return default


def _save(name, data):
    try:
        with open(os.path.join(BASE, name), "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


TG_ERR = ""


def norm_chat(c):
    c = str(c or "").strip()
    for pre in ("https://t.me/", "http://t.me/", "t.me/", "telegram.me/"):
        if c.lower().startswith(pre):
            c = c[len(pre):]
    c = c.strip("/")
    if c and not c.lstrip("-").isdigit() and not c.startswith("@"):
        c = "@" + c
    return c


def tg_conf():
    f = _load("tg.json", {})
    tok = (f.get("token") or os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    cid = norm_chat(f.get("chat_id") or os.environ.get("TELEGRAM_CHAT_ID") or "")
    return tok, cid


def tg_explain(d):
    low = d.lower()
    if "chat not found" in low:
        return f"شناسه یا نام کانال اشتباه است، یا ربات عضو و ادمین کانال نیست ({d})"
    if any(k in low for k in ("not a member", "not enough rights", "no rights to send", "administrator")):
        return f"ربات را به‌عنوان Administrator با مجوز Post Messages به کانال اضافه کنید ({d})"
    if "unauthorized" in low:
        return f"توکن ربات اشتباه است ({d})"
    return d


def tg_send(text):
    global TG_ERR
    tok, cid = tg_conf()
    if not tok or not cid:
        TG_ERR = "توکن ربات یا شناسه چت تنظیم نشده است"
        return False
    try:
        body = _json.dumps({"chat_id": cid, "text": text, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=body,
                                     headers={"content-type": "application/json"})
        urllib.request.urlopen(req, timeout=15, context=_ssl_ctx()).read()
        TG_ERR = ""
        return True
    except urllib.error.HTTPError as e:
        try:
            d = _json.loads(e.read().decode()).get("description", "")
        except Exception:
            d = str(e)
        TG_ERR = tg_explain(d)
    except Exception as e:
        TG_ERR = f"اتصال VPS به api.telegram.org ناموفق بود ({e})"
    return False


@app.post("/api/telegram/test")
def tg_test():
    if tg_send("🔔 <b>تست ربات تلگرام</b>\nپل MT5 با موفقیت به تلگرام وصل است."):
        return jsonify(ok=1)
    return jsonify(error="ارسال ناموفق: " + TG_ERR), 400


@app.get("/api/telegram/chatid")
def tg_chatid():
    tok, _ = tg_conf()
    if not tok:
        return jsonify(error="توکن ربات تنظیم نشده است"), 400
    try:
        j = _json.loads(http_get(f"https://api.telegram.org/bot{tok}/getUpdates"))
    except Exception as ex:
        return jsonify(error=f"ارتباط با تلگرام ناموفق بود: {ex}"), 502
    ids = {}
    for u in j.get("result", []):
        c = (u.get("message") or u.get("channel_post") or {}).get("chat", {})
        if c.get("id"):
            ids[c["id"]] = c.get("first_name") or c.get("title") or ""
    return jsonify([dict(id=k, name=v) for k, v in ids.items()])


@app.get("/api/tgconfig")
def tgconfig_get():
    tok, cid = tg_conf()
    return jsonify(has_token=bool(tok), token_tail=tok[-4:] if tok else "", chat_id=cid,
                   notify_close=bool(_load("tg.json", {}).get("notify_close", True)),
                   notify_open=bool(_load("tg.json", {}).get("notify_open", True)))


@app.post("/api/tgconfig")
def tgconfig_set():
    d = request.get_json(force=True)
    cur = {} if d.get("clear") else _load("tg.json", {})
    if d.get("token"):
        cur["token"] = str(d["token"]).strip()
    if "chat_id" in d:
        cur["chat_id"] = norm_chat(d["chat_id"])
    if "notify_close" in d:
        cur["notify_close"] = bool(d["notify_close"])
    if "notify_open" in d:
        cur["notify_open"] = bool(d["notify_open"])
    _save("tg.json", cur)
    return tgconfig_get()


# ---- سرویس هوش مصنوعی: Anthropic یا هر API سازگار با OpenAI (OpenAI، DeepSeek، OpenRouter، سرویس‌های ایرانی و...) ----
def ai_conf():
    f = _load("ai.json", {})
    prov = (f.get("provider") or os.environ.get("AI_PROVIDER") or "anthropic").lower()
    key = f.get("key") or os.environ.get("AI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or ""
    base = f.get("base_url") or os.environ.get("AI_BASE_URL") or ""
    model = f.get("model") or os.environ.get("AI_MODEL") or ("claude-sonnet-5-5" if prov == "anthropic" else "")
    return dict(provider=prov, key=key, base_url=base, model=model)


def call_llm(prompt):
    c = ai_conf()
    if not c["key"]:
        raise RuntimeError("سرویس هوش مصنوعی تنظیم نشده است (در اپ: تحلیل هوشمند ← تنظیم سرویس)")
    if c["provider"] == "anthropic":
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": c["key"], "anthropic-version": "2023-06-01", "content-type": "application/json"}
        body = {"model": c["model"], "max_tokens": 4500, "messages": [{"role": "user", "content": prompt}]}
    else:
        if not c["base_url"] or not c["model"]:
            raise RuntimeError("آدرس سرویس (Base URL) و نام مدل را وارد کنید")
        base = c["base_url"].rstrip("/")
        url = base if base.endswith("/chat/completions") else base + "/chat/completions"
        headers = {"Authorization": "Bearer " + c["key"], "content-type": "application/json"}
        body = {"model": c["model"], "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(url, data=_json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=170) as r:
            j = _json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"خطای سرویس هوش مصنوعی {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}")
    except Exception as e:
        raise RuntimeError(f"اتصال به سرویس هوش مصنوعی ناموفق بود: {e}")
    if c["provider"] == "anthropic":
        return "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
    try:
        return j["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError("پاسخ سرویس قالب OpenAI نداشت: " + _json.dumps(j, ensure_ascii=False)[:200])


@app.get("/api/aiconfig")
def aiconfig_get():
    c = ai_conf()
    return jsonify(provider=c["provider"], base_url=c["base_url"], model=c["model"], has_key=bool(c["key"]))


@app.post("/api/aiconfig")
def aiconfig_set():
    d = request.get_json(force=True)
    cur = {} if d.get("clear") else _load("ai.json", {})
    for k in ("provider", "base_url", "model"):
        if k in d:
            cur[k] = str(d[k]).strip()
    if d.get("key"):
        cur["key"] = str(d["key"]).strip()
    _save("ai.json", cur)
    for k in [k for k in CACHE if str(k).startswith("ai|")]:
        del CACHE[k]
    return aiconfig_get()


@app.post("/api/aitest")
def aitest():
    try:
        return jsonify(text=call_llm("فقط یک کلمه بنویس: OK")[:200])
    except Exception as ex:
        return jsonify(error=str(ex)), 502


TALERTS = _load("talerts.json", [])
tlock = threading.Lock()


@app.post("/api/talerts")
def talerts_add():
    d = request.get_json(force=True)
    sym, price = d["symbol"], float(d["price"])
    ref = d.get("ref")
    if ref is None:
        t = BE.tick(sym)
        ref = t["bid"] if t else price
    with tlock:
        TALERTS.append(dict(id=d["id"], symbol=sym, price=price, up=price >= float(ref), pf=BE.name.lower()))
        _save("talerts.json", TALERTS)
    return jsonify(ok=1)


@app.post("/api/talerts/delete")
def talerts_del():
    global TALERTS
    with tlock:
        TALERTS = [a for a in TALERTS if a["id"] != request.get_json(force=True)["id"]]
        _save("talerts.json", TALERTS)
    return jsonify(ok=1)


def talerts_step():
    with tlock:
        hit = []
        for a in TALERTS:
            try:
                t = (BACKENDS.get(a.get("pf")) or DEFAULT).tick(a["symbol"])
            except BErr:
                t = None
            if t and ((a["up"] and t["bid"] >= a["price"]) or (not a["up"] and t["bid"] <= a["price"])):
                hit.append((a, t["bid"]))
        for a, p in hit:
            tg_send(f"🔔 <b>هشدار قیمت {a['symbol']}</b>\nقیمت به {a['price']} رسید (الان {p})")
            TALERTS.remove(a)
        if hit:
            _save("talerts.json", TALERTS)
    return len(hit)


GUARD = dict(enabled=False, mode="warn", before=5, after=2, warn=15, impact="High")
GUARD.update(_load("guard.json", {}))
GLOG, GDONE = [], set()


def glog(msg):
    GLOG.append(dict(t=int(time.time()), msg=msg))
    del GLOG[:-40]


def guard_step(now=None):
    """هر ۱۵ ثانیه: معامله‌های باز را با خبرهای مهم نزدیک مقایسه می‌کند."""
    if not GUARD["enabled"] or BE.account() is None:
        return 0
    now = now or time.time()
    try:
        cal = cached("cal", 1800, load_calendar)
    except Exception:
        return 0
    imp = ("High", "Medium") if GUARD["impact"] == "Medium" else ("High",)
    evs = [e for e in cal if e["impact"] in imp]
    acted = 0
    try:
        plist = BE.positions()[0]
    except BErr:
        return 0
    for p in plist:
        cc = ccy_of(p["symbol"])
        for e in sorted(evs, key=lambda x: x["ts"]):
            if e["country"] not in cc:
                continue
            dt = e["ts"] - now
            if dt > GUARD["warn"] * 60 or dt < -GUARD["after"] * 60:
                continue
            label = f"{e['country']} · {e['title']} ({iran(e['ts'])} به وقت ایران)"
            if dt > GUARD["before"] * 60 or GUARD["mode"] != "close":
                k = (BE.name, p["ticket"], e["ts"], "w")
                if k not in GDONE and dt > 0:
                    GDONE.add(k)
                    glog(f"هشدار برای {p['symbol']}: خبر {label}")
                    tg_send(f"⚠️ <b>خبر مهم نزدیک است</b>\n{p['symbol']} باز است\n{label}\nحدود {int(dt // 60)} دقیقه دیگر")
                continue
            g = guard()
            if g:
                k = (BE.name, p["ticket"], e["ts"], "b")
                if k not in GDONE:
                    GDONE.add(k)
                    glog(f"بستن {p['symbol']} ممکن نشد: {g}")
                break
            IGNORE[(BE.name.lower(), p["ticket"])] = time.time()
            try:
                BE.close(p["ticket"], None, True)
                ok = True
            except Exception:
                IGNORE.pop((BE.name.lower(), p["ticket"]), None)
                ok = False
            glog(f"{'بسته شد' if ok else 'بستن ناموفق'}: {p['symbol']} #{p['ticket']} قبل از خبر {label}")
            tg_send(f"{'🛡 معامله بسته شد' if ok else '❗ بستن ناموفق'}\n{p['symbol']} #{p['ticket']}\nقبل از خبر {label}")
            acted += 1 if ok else 0
            break
    return acted


@app.get("/api/guard")
def guard_get():
    return jsonify(dict(GUARD, log=GLOG[-20:]))


@app.post("/api/guard")
def guard_set():
    d = request.get_json(force=True)
    if "enabled" in d:
        GUARD["enabled"] = bool(d["enabled"])
    if d.get("mode") in ("warn", "close"):
        GUARD["mode"] = d["mode"]
    if d.get("impact") in ("High", "Medium"):
        GUARD["impact"] = d["impact"]
    for k, lo, hi in (("before", 1, 60), ("after", 0, 30), ("warn", 1, 120)):
        if k in d:
            GUARD[k] = max(lo, min(hi, int(d[k])))
    _save("guard.json", {k: GUARD[k] for k in ("enabled", "mode", "before", "after", "warn", "impact")})
    glog(f"تنظیمات محافظ: {'فعال' if GUARD['enabled'] else 'غیرفعال'} · {GUARD['mode']}")
    return jsonify(dict(GUARD, log=GLOG[-20:]))


# ===================== تریلینگ استاپ روی سرور =====================
# با بسته بودن اپ هم کار می‌کند. هر قانون: پلتفرم، تیکت، فاصله (به واحد قیمت) و حداقل جابه‌جایی SL.
TRAILS = _load("trails.json", [])
trlock = threading.Lock()
CLOSED_TRAIL = set()


@app.get("/api/trail")
def trail_list():
    pf = BE.name.lower()
    return jsonify([dict(ticket=t["ticket"], dist=t["dist"], symbol=t.get("symbol")) for t in TRAILS if t["pf"] == pf])


@app.post("/api/trail")
def trail_set():
    g = guard()
    if g:
        return jsonify(error=g), 403
    d = request.get_json(force=True)
    ticket, dist = int(d["ticket"]), float(d["dist"])
    if dist <= 0:
        return jsonify(error="فاصله تریلینگ نامعتبر است"), 400
    p = next((x for x in BE.positions()[0] if x["ticket"] == ticket), None)
    if p is None:
        return jsonify(error="معامله پیدا نشد"), 404
    if not p["sl"]:
        return jsonify(error="اول برای معامله SL بگذارید"), 400
    pf = BE.name.lower()
    step = float(d.get("step") or 0) or dist * 0.1
    with trlock:
        TRAILS[:] = [t for t in TRAILS if not (t["pf"] == pf and t["ticket"] == ticket)]
        TRAILS.append(dict(pf=pf, ticket=ticket, symbol=p["symbol"], dist=dist, step=step, since=int(time.time())))
        _save("trails.json", TRAILS)
    return jsonify(ok=1)


@app.post("/api/trail/delete")
def trail_del():
    ticket, pf = int(request.get_json(force=True)["ticket"]), BE.name.lower()
    with trlock:
        TRAILS[:] = [t for t in TRAILS if not (t["pf"] == pf and t["ticket"] == ticket)]
        _save("trails.json", TRAILS)
    return jsonify(ok=1)


def trail_step():
    for k, b in list(BACKENDS.items()):
        rules = [t for t in TRAILS if t["pf"] == k]
        if not rules:
            continue
        _tl.be = b
        try:
            if b.account() is None or guard():
                continue
            try:
                ps = {p["ticket"]: p for p in b.positions()[0]}
            except BErr:
                continue
            for r in rules:
                p = ps.get(r["ticket"])
                if p is None:
                    with trlock:
                        if r in TRAILS:
                            TRAILS.remove(r)
                            _save("trails.json", TRAILS)
                    CLOSED_TRAIL.add((k, r["ticket"]))
                    continue
                if not p["sl"] or time.time() < r.get("retry", 0):
                    continue
                try:
                    t = b.tick(p["symbol"])
                    if not t:
                        continue
                    d = 1 if p["side"] == "B" else -1
                    price = t["bid"] if d == 1 else t["ask"]
                    info = b.symbol_info(p["symbol"])
                    ns = round(price - d * r["dist"], info["digits"] if info else 5)
                    if (ns - p["sl"]) * d > r["step"]:
                        b.modify(p["ticket"], ns, p["tp"] or 0)
                except BErr as e:
                    r["retry"] = time.time() + 10
                    if not r.get("logged"):
                        r["logged"] = True
                        glog(f"تریلینگ {p['symbol']} #{p['ticket']} ناموفق: {e.msg}")
        finally:
            _tl.be = None


# ===================== اعلان تلگرام وقتی معامله بسته شد (TP / SL / ...) =====================
KNOWN, PENDING_CL, IGNORE, MANUAL = {}, [], {}, {}


def _tg_on(key):
    return bool(_load("tg.json", {}).get(key, True))


def _side_fa(p):
    return "خرید" if p["side"] == "B" else "فروش"


def _notify_open(k, p):
    if not _tg_on("notify_open"):
        return
    tag = AUTOTAG.pop((k, p["ticket"]), None)
    lines = ["🤖 <b>معامله خودکار باز شد</b>" if tag else "📈 <b>معامله باز شد</b>",
             f"{p['symbol']} · {_side_fa(p)} · {p['lots']} لات · {BACKENDS[k].name} #{p['ticket']}",
             f"قیمت ورود: {p['price']}"]
    lv = []
    if p.get("sl"):
        lv.append(f"SL: {p['sl']}")
    if p.get("tp"):
        lv.append(f"TP: {p['tp']}")
    if lv:
        lines.append(" · ".join(lv))
    if tag:
        lines.append("دلیل: " + tag)
    tg_send("\n".join(lines))


def _resolve_close(it, now):
    k, p = it["pf"], it["pos"]
    b = BACKENDS[k]
    partial = it.get("partial")
    deals = []
    try:
        deals = [d for d in b.history(int(now) - 3 * 86400, int(now) + 600) if d["pos"] == p["ticket"]]
    except BErr:
        pass
    outs = [d for d in deals if d["entry"] != 0]
    if partial:  # فقط معامله‌ی بخشی همین لحظه (نه بخشی‌های قبلی)
        outs = [d for d in outs if d["time"] >= it["t0"] - 15]
    if not outs:
        it["tries"] += 1
        if it["tries"] < 8:
            return False
    out = outs[-1] if outs else None
    price = out["price"] if out else None
    if out:
        total = (out["profit"] + out["comm"] + out["swap"] + out.get("fee", 0)) if partial else \
            sum(d["profit"] + d["comm"] + d["swap"] + d.get("fee", 0) for d in deals)
    else:
        total = p.get("profit", 0)
    reason = out.get("reason") if out else None
    kind = "close"
    if out:
        tol = abs(price) * 0.0005
        if reason == 5 or (reason is None and p["tp"] and abs(price - p["tp"]) <= tol):
            kind = "tp"
        elif reason == 4 or (reason is None and p["sl"] and abs(price - p["sl"]) <= tol):
            kind = "sl"
        elif reason == 6:
            kind = "so"
        elif it.get("manual"):
            kind = "manual"
    elif it.get("manual"):
        kind = "manual"
    if it.get("manual") and kind in ("tp", "sl") and reason is None:
        kind = "manual"  # قیمت نزدیک TP/SL بوده ولی خودمان از اپ بسته‌ایم
    if _tg_on("notify_close"):
        trail = it.get("trail")
        head = {"tp": "✅ <b>TP فعال شد</b>", "sl": "🛑 <b>SL فعال شد</b>" + (" (تریلینگ)" if trail else ""),
                "so": "⚠️ <b>Stop Out (بسته شدن اجباری)</b>", "manual": "🔒 <b>معامله بسته شد (دستی)</b>",
                "close": "ℹ️ <b>معامله بسته شد</b>"}[kind]
        if partial:
            head = "✂️ <b>بخشی از معامله بسته شد</b>" + {"tp": " · TP", "sl": " · SL"}.get(kind, "")
        try:
            cur = (b.account() or {}).get("currency", "")
        except Exception:
            cur = ""
        lots = partial if partial else p["lots"]
        lines = [head, f"{p['symbol']} · {_side_fa(p)} · {lots} لات · {b.name} #{p['ticket']}"]
        lines.append(f"ورود {p['price']}" + (f" ← خروج {price}" if price is not None else ""))
        lines.append(f"{'سود' if total >= 0 else 'زیان'}: {total:+.2f} {cur}".strip() + ("" if out else " (تقریبی)"))
        if partial:
            lines.append(f"باقی‌مانده: {it.get('remnant')} لات")
        tg_send("\n".join(lines))
    return True


def closes_step():
    now = time.time()
    for key in [x for x, t in IGNORE.items() if now - t > 3600]:
        IGNORE.pop(key, None)
    for key in [x for x, t in MANUAL.items() if now - t > 3600]:
        MANUAL.pop(key, None)
    for k, b in list(BACKENDS.items()):
        _tl.be = b
        try:
            if b.account() is None:
                continue
            try:
                cur = {p["ticket"]: p for p in b.positions()[0]}
            except BErr:
                continue
            prev = KNOWN.get(k)
            KNOWN[k] = cur
            if prev is None:
                continue
            gone = [p for t, p in prev.items() if t not in cur]
            new = [p for t, p in cur.items() if t not in prev]
            # MT4: بستن بخشی = تیکت قدیمی بسته می‌شود و تیکت جدید با لات کمتر می‌آید
            for n in list(new):
                m = next((g for g in gone if g["symbol"] == n["symbol"] and g["side"] == n["side"]
                          and abs(g["price"] - n["price"]) < 1e-9 and g["lots"] > n["lots"] + 1e-9), None)
                if m:
                    new.remove(n)
                    gone.remove(m)
                    with trlock:
                        for r in TRAILS:
                            if r["pf"] == k and r["ticket"] == m["ticket"]:
                                r["ticket"] = n["ticket"]
                        _save("trails.json", TRAILS)
                    PENDING_CL.append(dict(pf=k, pos=m, tries=0, t0=now, partial=round(m["lots"] - n["lots"], 3),
                                           remnant=n["lots"], manual=MANUAL.pop((k, m["ticket"]), None) is not None,
                                           trail=False))
            # MT5: بستن بخشی = همان تیکت با لات کمتر
            for t, p in cur.items():
                q = prev.get(t)
                if q and q["lots"] > p["lots"] + 1e-9:
                    PENDING_CL.append(dict(pf=k, pos=q, tries=0, t0=now, partial=round(q["lots"] - p["lots"], 3),
                                           remnant=p["lots"], manual=MANUAL.pop((k, t), None) is not None, trail=False))
            for p in gone:
                t = p["ticket"]
                if IGNORE.pop((k, t), None):
                    continue
                trail = (k, t) in CLOSED_TRAIL or any(r["pf"] == k and r["ticket"] == t for r in TRAILS)
                CLOSED_TRAIL.discard((k, t))
                PENDING_CL.append(dict(pf=k, pos=p, tries=0, t0=now, trail=trail,
                                       manual=MANUAL.pop((k, t), None) is not None))
            for p in new:
                try:
                    _notify_open(k, p)
                except Exception:
                    pass
        finally:
            _tl.be = None
    keep = []
    for it in PENDING_CL:
        _tl.be = BACKENDS[it["pf"]]
        try:
            done = _resolve_close(it, now)
        except Exception:
            it["tries"] += 1
            done = it["tries"] >= 8
        finally:
            _tl.be = None
        if not done:
            keep.append(it)
    PENDING_CL[:] = keep


# ===================== هشدار برخورد قیمت به EMA / SMA (جایگزین اکسپرت EMA_MultiPair_Alert) =====================
# روی کندل بسته‌شده‌ی آخر هر نماد و تایم‌فریم بررسی می‌شود؛ برای هر کندل فقط یک‌بار هشدار می‌دهد.
EMA_DEF = dict(enabled=False, platform="", symbols="EURUSD,GBPUSD,USDJPY,USDCHF,USDCAD,AUDUSD,NZDUSD",
               fast=200, slow=1000, interval=30, buffer_on=True, buffer_pips=2.0, sma_on=False, sma=200,
               tfs=dict(h1=dict(f=False, s=False), h4=dict(f=True, s=True), d1=dict(f=True, s=True),
                        w1=dict(f=True, s=True)))
EMA_TFL = {"h1": "1H", "h4": "4H", "d1": "Daily", "w1": "Weekly"}
EMA_CFG = {}
EMA_STATE = _load("ema_state.json", {})  # {"init": bool, "sent": {key: barTime}}
EMA_STATE.setdefault("sent", {})
EMA_LOG, EMA_ERR, EMA_SEEN = [], {}, {}
EMA_FORCE = [False]
emalock = threading.Lock()


def _ema_merge(src):
    c = _json.loads(_json.dumps(EMA_DEF))
    for k in c:
        if k == "tfs":
            for tk in c["tfs"]:
                t = (src.get("tfs") or {}).get(tk) or {}
                c["tfs"][tk]["f"] = bool(t.get("f", c["tfs"][tk]["f"]))
                c["tfs"][tk]["s"] = bool(t.get("s", c["tfs"][tk]["s"]))
        elif k in src:
            c[k] = src[k]
    return c


def _ema_valid(d):
    import re
    out = _ema_merge(d)
    syms = [x.strip() for x in str(out["symbols"]).replace("\n", ",").replace(";", ",").split(",") if x.strip()]
    if len(syms) > 40 or any(not re.fullmatch(r"[A-Za-z0-9._#\-+]{1,32}", x) for x in syms):
        raise BErr("لیست نمادها نامعتبر است (حداکثر ۴۰ نماد، با کاما جدا کنید)", 400)
    out["symbols"] = ",".join(syms)
    for k, lo, hi in (("fast", 2, 5000), ("slow", 2, 5000), ("sma", 2, 5000), ("interval", 10, 3600)):
        try:
            out[k] = int(out[k])
        except Exception:
            raise BErr(f"مقدار {k} نامعتبر است", 400)
        if not lo <= out[k] <= hi:
            raise BErr(f"مقدار {k} باید بین {lo} و {hi} باشد", 400)
    try:
        out["buffer_pips"] = float(out["buffer_pips"])
    except Exception:
        raise BErr("مقدار بافر نامعتبر است", 400)
    if not 0 <= out["buffer_pips"] <= 1000:
        raise BErr("بافر باید بین ۰ و ۱۰۰۰ پیپ باشد", 400)
    out["enabled"], out["buffer_on"], out["sma_on"] = bool(out["enabled"]), bool(out["buffer_on"]), bool(out["sma_on"])
    if out["platform"] not in BACKENDS:
        out["platform"] = ""
    return out


EMA_CFG.update(_ema_merge(_load("ema.json", {})))


@app.get("/api/ema")
def ema_get():
    c = dict(EMA_CFG)
    c["platform_eff"] = c["platform"] or DEFAULT.name.lower()
    return jsonify(cfg=c, log=EMA_LOG[-30:][::-1], errors=EMA_ERR, platforms=list(BACKENDS))


@app.post("/api/ema")
def ema_set():
    d = request.get_json(force=True)
    c = _ema_valid(d)
    with emalock:
        EMA_CFG.clear()
        EMA_CFG.update(c)
        _save("ema.json", c)
        EMA_SEEN.clear()
        EMA_FORCE[0] = True
    return jsonify(ok=1)


@app.post("/api/ema/scan")
def ema_scan():
    EMA_SEEN.clear()
    EMA_FORCE[0] = True
    return jsonify(ok=1)


def _ema_last(closes, period):
    k, e = 2.0 / (period + 1), closes[0]
    for c in closes[1:]:
        e = k * c + (1 - k) * e
    return e


def _sma_last(closes, period):
    return sum(closes[-period:]) / period


def _ema_alert(b, sym, tfk, name, level, bt, row, baseline, digits):
    key = f"{sym}|{tfk}|{name}"
    if EMA_STATE["sent"].get(key) == bt:
        return
    EMA_STATE["sent"][key] = bt
    if baseline:
        return
    txt = f"{sym} | {EMA_TFL[tfk]} | price touched {name}"
    EMA_LOG.append(dict(t=int(time.time()), sym=sym, tf=EMA_TFL[tfk], name=name, text=txt))
    del EMA_LOG[:-100]
    tg_send(f"🔔 <b>{sym} | {EMA_TFL[tfk]}</b>\nقیمت به {name} برخورد کرد\n"
            f"{name}: {level:.{digits}f} · کندل بسته‌شده: H {row[2]:.{digits}f} / L {row[3]:.{digits}f}"
            f"\n{b.name}")
    try:
        auto_trade_from_alert(b, sym, tfk, name, level, row)
    except Exception as e:
        glog(f"معامله خودکار خطا: {e}")


def _ema_check(b, sym, tfk, cfg, baseline):
    t = cfg["tfs"][tfk]
    want = []
    if t["f"]:
        want.append(("EMA", cfg["fast"]))
    if t["s"]:
        want.append(("EMA", cfg["slow"]))
    if cfg["sma_on"] and tfk in ("d1", "w1"):
        want.append(("SMA", cfg["sma"]))
    if not want:
        return
    d, rows = b.candles(sym, tfk, 3)
    if len(rows) < 2:
        return
    bt = rows[-2][0]
    if EMA_SEEN.get((sym, tfk)) == bt:
        return
    need = max(p for _, p in want)
    d, rows = b.candles(sym, tfk, min(6000, max(need * 3 + 50, need + 20)))
    closed = rows[:-1]  # آخرین کندل در حال تشکیل است
    if len(closed) < 2:
        return
    bt, row = closed[-1][0], closed[-1]
    info = b.symbol_info(sym) or {}
    digits = info.get("digits", d)
    pip = 10 ** -digits * (10 if digits in (3, 5) else 1)
    buf = cfg["buffer_pips"] * pip if cfg["buffer_on"] else 0.0
    closes = [r[4] for r in closed]
    for kind, per in want:
        if len(closed) < per + 1:
            continue  # تاریخچه کافی نیست
        lvl = _ema_last(closes, per) if kind == "EMA" else _sma_last(closes, per)
        if row[3] <= lvl + buf and row[2] >= lvl - buf:
            _ema_alert(b, sym, tfk, f"{kind}{per}", lvl, bt, row, baseline, digits)
    EMA_SEEN[(sym, tfk)] = bt


def ema_scan_once():
    cfg = dict(EMA_CFG)
    pf = cfg["platform"] or DEFAULT.name.lower()
    b = BACKENDS.get(pf)
    if b is None:
        return
    _tl.be = b
    try:
        if b.account() is None:
            return
        baseline = not EMA_STATE.get("init")
        for sym in [x for x in cfg["symbols"].split(",") if x]:
            for tfk in ("h1", "h4", "d1", "w1"):
                if not EMA_CFG["enabled"]:
                    return
                try:
                    _ema_check(b, sym, tfk, cfg, baseline)
                    EMA_ERR.pop(sym, None)
                except BErr as e:
                    EMA_ERR[sym] = e.msg
                except Exception as e:
                    EMA_ERR[sym] = str(e)[:80]
        if baseline:
            EMA_STATE["init"] = True
        _save("ema_state.json", EMA_STATE)
    finally:
        _tl.be = None


def ema_loop():
    last = 0.0
    while True:
        time.sleep(1)
        try:
            if not EMA_CFG.get("enabled"):
                continue
            if EMA_FORCE[0] or time.time() - last >= max(10, EMA_CFG["interval"]):
                EMA_FORCE[0] = False
                last = time.time()
                ema_scan_once()
        except Exception:
            pass


# ===================== SL/TP خودکار و معامله خودکار (جایگزین بخش‌های اکسپرت EMA_MultiPair_Alert) =====================
AUTO_DEF = dict(
    sltp=dict(enabled=False, sl_on=True, tp_on=True, sl_mode="pips", sl_val=20.0, tp_mode="rr", tp_val=2.0, symbols=""),
    trade=dict(enabled=False, lot_mode="fixed", lot=0.01, risk_pct=1.0, sl_mode="pips", sl_val=20.0, tp_mode="pips",
               tp_val=40.0, invert=False, one_per_symbol=True, tfs=dict(h1=False, h4=True, d1=True, w1=True)),
    rules=dict(enabled=True, max_open_on=True, max_open=5, daily_loss_on=True, daily_loss_pct=5.0, max_risk_on=True,
               max_risk_pct=2.0, apply_manual=False),
)
AUTO_CFG = {}
AUTOTAG, AUTOSLTP_TRIES = {}, {}
autolock = threading.Lock()


def _auto_merge(src):
    c = _json.loads(_json.dumps(AUTO_DEF))
    for grp, d in c.items():
        sg = (src or {}).get(grp) or {}
        for k, dv in d.items():
            if k == "tfs":
                for tk in dv:
                    dv[tk] = bool((sg.get("tfs") or {}).get(tk, dv[tk]))
            elif k in sg:
                d[k] = sg[k]
    return c


def _auto_valid(src):
    c = _auto_merge(src)
    def num(grp, k, lo, hi):
        try:
            c[grp][k] = float(c[grp][k])
        except Exception:
            raise BErr(f"مقدار {k} نامعتبر است", 400)
        if not lo <= c[grp][k] <= hi:
            raise BErr(f"مقدار {k} باید بین {lo} و {hi} باشد", 400)
    for grp in ("sltp", "trade"):
        for k in ("sl_val", "tp_val"):
            num(grp, k, 0, 1e7)
        if c[grp]["sl_mode"] not in ("pips", "usd"):
            raise BErr("حالت SL نامعتبر است", 400)
        if c[grp]["tp_mode"] not in ("pips", "usd", "rr"):
            raise BErr("حالت TP نامعتبر است", 400)
    import re
    syms = [x.strip() for x in str(c["sltp"]["symbols"]).replace(";", ",").split(",") if x.strip()]
    if len(syms) > 40 or any(not re.fullmatch(r"[A-Za-z0-9._#\-+]{1,32}", x) for x in syms):
        raise BErr("لیست نمادهای SL/TP خودکار نامعتبر است", 400)
    c["sltp"]["symbols"] = ",".join(syms)
    t = c["trade"]
    num("trade", "lot", 0.001, 1000)
    num("trade", "risk_pct", 0.01, 100)
    if t["lot_mode"] not in ("fixed", "risk"):
        raise BErr("حالت حجم نامعتبر است", 400)
    if t["lot_mode"] == "risk" and t["sl_mode"] == "usd":
        raise BErr("با حجم بر اساس ریسک، SL باید بر حسب پیپ باشد", 400)
    if t["enabled"] and t["sl_val"] <= 0:
        raise BErr("برای معامله خودکار، SL الزامی است", 400)
    if c["sltp"]["enabled"] and c["sltp"]["sl_on"] and c["sltp"]["sl_val"] <= 0:
        raise BErr("مقدار SL خودکار باید بیشتر از صفر باشد", 400)
    r = c["rules"]
    num("rules", "max_open", 1, 500)
    num("rules", "daily_loss_pct", 0.1, 100)
    num("rules", "max_risk_pct", 0.1, 100)
    r["max_open"] = int(r["max_open"])
    for grp in c:
        for k, v in c[grp].items():
            if isinstance(AUTO_DEF[grp][k], bool):
                c[grp][k] = bool(v)
    return c


AUTO_CFG.update(_auto_merge(_load("auto.json", {})))


@app.get("/api/auto")
def auto_get():
    return jsonify(cfg=AUTO_CFG)


@app.post("/api/auto")
def auto_set():
    c = _auto_valid(request.get_json(force=True))
    with autolock:
        AUTO_CFG.clear()
        AUTO_CFG.update(c)
        _save("auto.json", c)
        AUTOSLTP_TRIES.clear()
    return jsonify(ok=1)


def _pipsize(info):
    d = info["digits"]
    return 10 ** -d * (10 if d in (3, 5) else 1)


def _pipvalue(info):  # دلار به ازای هر پیپ برای ۱ لات
    ts = info["tick_size"] or 10 ** -info["digits"]
    return info["tick_value"] * (_pipsize(info) / ts)


def _dist(mode, val, info, lots, sl_dist=None):
    """فاصله قیمتی از مبلغ پیپ / دلار / ریسک به ریوارد"""
    if mode == "pips":
        return val * _pipsize(info)
    if mode == "usd":
        if not info["tick_value"] or not lots:
            raise BErr("ارزش تیک نماد معلوم نیست؛ حالت دلار ممکن نیست", 400)
        return val * info["tick_size"] / (info["tick_value"] * lots)
    return val * (sl_dist or 0)


def _lots_ok(l, info):
    st = info["volume_step"] or 0.01
    l = int(l / st + 1e-9) * st
    l = max(l, info["volume_min"])
    l = min(l, info["volume_max"], MAX_LOT)
    return round(l, 3 if st < 0.01 else 2)


def _day_pnl(b):
    now = time.time()
    lt = time.localtime(now)
    day0 = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    realized = sum(d["profit"] + d["comm"] + d["swap"] + d.get("fee", 0) for d in b.history(int(day0), int(now) + 600))
    a = b.account()
    return realized, a["profit"], a["balance"]


def _rules_check(b, sym, lots, sl_dist, info, manual=False):
    r = AUTO_CFG["rules"]
    if not r["enabled"] or (manual and not r["apply_manual"]):
        return None
    a = b.account()
    if a is None:
        return None
    if r["max_open_on"] and len(b.positions()[0]) >= r["max_open"]:
        return f"تعداد معامله‌های باز به سقف {r['max_open']} رسیده است"
    if r["max_risk_on"] and sl_dist and info["tick_size"]:
        risk = lots * sl_dist / info["tick_size"] * info["tick_value"]
        lim = a["balance"] * r["max_risk_pct"] / 100
        if risk > lim:
            return f"ریسک این معامله {risk:.2f} است و از {r['max_risk_pct']}٪ موجودی ({lim:.2f}) بیشتر است"
    if r["daily_loss_on"]:
        realized, floating, bal = _day_pnl(b)
        loss = -(realized + floating)
        lim = (bal - realized) * r["daily_loss_pct"] / 100
        if loss >= lim > 0:
            return f"سقف ضرر روزانه {r['daily_loss_pct']}٪ رسیده است (ضرر امروز {loss:.2f})"
    return None


def _auto_open(b, sym, tfk, name, level, row):
    t = AUTO_CFG["trade"]
    g = guard()
    if g:
        return g
    info = b.symbol_info(sym)
    if info is None:
        return "نماد پیدا نشد"
    ps = b.positions()[0]
    if t["one_per_symbol"] and any(p["symbol"] == sym for p in ps):
        return "برای این نماد معامله باز وجود دارد"
    tk = b.tick(sym)
    if tk is None:
        return "قیمت نماد در دسترس نیست"
    side = "S" if row[1] < level else "B"  # مثل اکسپرت قبلی: کندل از زیر EMA آمده ← فروش، از بالا ← خرید
    if t["invert"]:
        side = "B" if side == "S" else "S"
    d = 1 if side == "B" else -1
    price = tk["ask"] if side == "B" else tk["bid"]
    a = b.account()
    if t["lot_mode"] == "risk":
        pv = _pipvalue(info)
        if not t["sl_val"] or not pv:
            return "محاسبه حجم از ریسک ممکن نشد"
        lots = a["equity"] * t["risk_pct"] / 100 / (t["sl_val"] * pv)
    else:
        lots = t["lot"]
    lots = _lots_ok(lots, info)
    if lots <= 0:
        return "حجم نامعتبر"
    sl_dist = _dist(t["sl_mode"], t["sl_val"], info, lots)
    tp_dist = _dist(t["tp_mode"], t["tp_val"], info, lots, sl_dist) if t["tp_val"] > 0 else 0
    if sl_dist <= 0:
        return "SL الزامی است"
    why = _rules_check(b, sym, lots, sl_dist, info)
    if why:
        return why
    dg = info["digits"]
    sl = round(price - d * sl_dist, dg)
    tp = round(price + d * tp_dist, dg) if tp_dist > 0 else 0
    r = b.order(sym, side, "mkt", lots, price, sl, tp)
    AUTOTAG[(b.name.lower(), r["ticket"])] = f"لمس {name} در {EMA_TFL[tfk]}"
    return None


def auto_trade_from_alert(b, sym, tfk, name, level, row):
    t = AUTO_CFG["trade"]
    if not t["enabled"] or not t["tfs"].get(tfk):
        return
    try:
        why = _auto_open(b, sym, tfk, name, level, row)
    except BErr as e:
        why = e.msg
    except Exception as e:
        why = str(e)[:120]
    if why:
        tg_send(f"🤖 <b>معامله خودکار انجام نشد</b>\n{sym} · {name} · {EMA_TFL[tfk]}\nدلیل: {why}")


def autosltp_step():
    c = AUTO_CFG["sltp"]
    if not c["enabled"] or not (c["sl_on"] or c["tp_on"]):
        return
    only = {x for x in c["symbols"].split(",") if x}
    for k, b in list(BACKENDS.items()):
        _tl.be = b
        try:
            if b.account() is None or guard():
                continue
            try:
                ps = b.positions()[0]
            except BErr:
                continue
            for p in ps:
                if only and p["symbol"] not in only:
                    continue
                need_sl, need_tp = c["sl_on"] and not p["sl"], c["tp_on"] and not p["tp"]
                key = (k, p["ticket"])
                if not (need_sl or need_tp) or AUTOSLTP_TRIES.get(key, 0) >= 3:
                    continue
                try:
                    info, tk = b.symbol_info(p["symbol"]), b.tick(p["symbol"])
                    if not info or not tk:
                        continue
                    AUTOSLTP_TRIES[key] = AUTOSLTP_TRIES.get(key, 0) + 1
                    d, entry, lots, dg = (1 if p["side"] == "B" else -1), p["price"], p["lots"], info["digits"]
                    cur = tk["bid"] if d == 1 else tk["ask"]
                    sl, tp = p["sl"] or 0, p["tp"] or 0
                    sl_dist = abs(entry - p["sl"]) if p["sl"] else _dist(c["sl_mode"], c["sl_val"], info, lots)
                    if need_sl:
                        sl = round(entry - d * sl_dist, dg)
                        if (cur - sl) * d <= 0:
                            sl = round(cur - d * sl_dist, dg)
                        if sl <= 0:
                            raise BErr("SL محاسبه‌شده نامعتبر است (فاصله خیلی زیاد)", 400)
                    if need_tp:
                        td = _dist(c["tp_mode"], c["tp_val"], info, lots, sl_dist)
                        if td > 0:
                            tp = round(entry + d * td, dg)
                            if (tp - cur) * d <= 0:
                                tp = round(cur + d * td, dg)
                    b.modify(p["ticket"], sl, tp)
                    AUTOSLTP_TRIES[key] = 99
                    tg_send(f"🛡 <b>SL/TP خودکار گذاشته شد</b>\n{p['symbol']} · {'خرید' if d == 1 else 'فروش'} · {lots} لات · {b.name} #{p['ticket']}\n"
                            f"SL: {sl or '—'} · TP: {tp or '—'}")
                except BErr as e:
                    if AUTOSLTP_TRIES[key] >= 3:
                        tg_send(f"🛡 SL/TP خودکار برای {p['symbol']} #{p['ticket']} ناموفق بود: {e.msg}")
        finally:
            _tl.be = None


def background_loop():
    n = 0
    while True:
        time.sleep(1)
        n += 1
        for fn, every in ((trail_step, 1), (autosltp_step, 3), (closes_step, 2), (talerts_step, 2), (_guard_all, 16)):
            if n % every:
                continue
            try:
                if fn is talerts_step and not TALERTS:
                    continue
                if fn is trail_step and not TRAILS:
                    continue
                fn()
            except Exception:
                pass


def _guard_all():
    for b in list(BACKENDS.values()):
        _tl.be = b
        try:
            guard_step()
        finally:
            _tl.be = None


if __name__ == "__main__":
    for b in BACKENDS.values():
        b.start()
    print("پلتفرم‌های فعال:", ", ".join(k.upper() for k in BACKENDS), "| پیش‌فرض:", DEFAULT.name)
    print("سقف حجم:", MAX_LOT, "| حساب واقعی مجاز:", ALLOW_REAL)
    threading.Thread(target=background_loop, daemon=True).start()
    threading.Thread(target=ema_loop, daemon=True).start()
    from waitress import serve
    if EA_PORT and EA_PORT != PORT and "mt4" in BACKENDS:
        try:
            print(f"پل روی 127.0.0.1:{PORT} و برای اکسپرت MT4 روی 127.0.0.1:{EA_PORT} گوش می‌دهد")
            serve(app, listen=f"127.0.0.1:{PORT} 127.0.0.1:{EA_PORT}", threads=16)
        except OSError as e:
            print(f"پورت {EA_PORT} در دسترس نیست ({e}). فقط روی {PORT} اجرا می‌شود؛ اکسپرت MT4 کار نخواهد کرد مگر EA_PORT را روی پورت آزاد 80 یا 443 بگذارید.")
            serve(app, host="127.0.0.1", port=PORT, threads=16)
    else:
        serve(app, host="127.0.0.1", port=PORT, threads=16)
