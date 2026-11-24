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
    IGNORE[k] = time.time()  # بستن عمدی از اپ؛ اعلان TP/SL نمی‌خواهد
    try:
        return jsonify(BE.close(int(d["ticket"]), d.get("volume")))
    except Exception:
        IGNORE.pop(k, None)
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
                   notify_close=bool(_load("tg.json", {}).get("notify_close", True)))


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
KNOWN, PENDING_CL, IGNORE = {}, [], {}


def _resolve_close(it, now):
    k, p = it["pf"], it["pos"]
    b = BACKENDS[k]
    deals = []
    try:
        deals = [d for d in b.history(int(now) - 3 * 86400, int(now) + 600) if d["pos"] == p["ticket"]]
    except BErr:
        pass
    outs = [d for d in deals if d["entry"] != 0]
    if not outs:
        it["tries"] += 1
        if it["tries"] < 8:
            return False
    out = outs[-1] if outs else None
    price = out["price"] if out else None
    total = sum(d["profit"] + d["comm"] + d["swap"] + d.get("fee", 0) for d in deals) if out else p.get("profit", 0)
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
    if _load("tg.json", {}).get("notify_close", True):
        trail = it.get("trail")
        head = {"tp": "✅ <b>TP فعال شد</b>", "sl": "🛑 <b>SL فعال شد</b>" + (" (تریلینگ)" if trail else ""),
                "so": "⚠️ <b>Stop Out (بسته شدن اجباری)</b>", "close": "ℹ️ <b>معامله بسته شد</b>"}[kind]
        try:
            cur = (b.account() or {}).get("currency", "")
        except Exception:
            cur = ""
        side = "خرید" if p["side"] == "B" else "فروش"
        lines = [head, f"{p['symbol']} · {side} · {p['lots']} لات · {b.name} #{p['ticket']}"]
        lines.append(f"ورود {p['price']}" + (f" ← خروج {price}" if price is not None else ""))
        lines.append(f"{'سود' if total >= 0 else 'زیان'}: {total:+.2f} {cur}".strip() + ("" if out else " (تقریبی)"))
        tg_send("\n".join(lines))
    return True


def closes_step():
    now = time.time()
    for k in [x for x, t in IGNORE.items() if now - t > 3600]:
        IGNORE.pop(k, None)
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
            for t, p in prev.items():
                if t in cur:
                    continue
                if IGNORE.pop((k, t), None):
                    continue
                trail = (k, t) in CLOSED_TRAIL or any(r["pf"] == k and r["ticket"] == t for r in TRAILS)
                CLOSED_TRAIL.discard((k, t))
                PENDING_CL.append(dict(pf=k, pos=p, tries=0, trail=trail))
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


def background_loop():
    n = 0
    while True:
        time.sleep(1)
        n += 1
        for fn, every in ((trail_step, 1), (closes_step, 2), (talerts_step, 2), (_guard_all, 16)):
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
