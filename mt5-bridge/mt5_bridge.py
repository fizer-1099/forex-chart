"""پل بین اپ و MT5 روی VPS ویندوزی.
- فقط روی 127.0.0.1 گوش می‌دهد؛ با Cloudflare Tunnel (HTTPS) در دسترس اپ قرار می‌گیرد.
- همه درخواست‌ها نیاز به هدر X-Token دارند.
- پیش‌فرض: فقط حساب دمو. حساب واقعی فقط با ALLOW_REAL=1.
"""
import os, time, hmac, threading
from flask import Flask, request, jsonify
from flask_cors import CORS
import MetaTrader5 as mt5

TOKEN = os.environ.get("BRIDGE_TOKEN", "")
MAX_LOT = float(os.environ.get("MAX_LOT", "0.1"))
ALLOW_REAL = os.environ.get("ALLOW_REAL", "0") == "1"
UTC_OFFSET_H = os.environ.get("MT5_UTC_OFFSET")  # اختلاف ساعت سرور بروکر با UTC (ساعت)
PORT = int(os.environ.get("BRIDGE_PORT", "8787"))
MAGIC = 777
if len(TOKEN) < 24:
    raise SystemExit("BRIDGE_TOKEN باید حداقل ۲۴ کاراکتر باشد")

app = Flask(__name__)
CORS(app, allow_headers=["X-Token", "Content-Type"])
lock = threading.Lock()

TF = {"m1": mt5.TIMEFRAME_M1, "m2": mt5.TIMEFRAME_M2, "m3": mt5.TIMEFRAME_M3, "m4": mt5.TIMEFRAME_M4,
      "m5": mt5.TIMEFRAME_M5, "m6": mt5.TIMEFRAME_M6, "m10": mt5.TIMEFRAME_M10, "m12": mt5.TIMEFRAME_M12,
      "m15": mt5.TIMEFRAME_M15, "m20": mt5.TIMEFRAME_M20, "m30": mt5.TIMEFRAME_M30,
      "h1": mt5.TIMEFRAME_H1, "h2": mt5.TIMEFRAME_H2, "h3": mt5.TIMEFRAME_H3, "h4": mt5.TIMEFRAME_H4,
      "h6": mt5.TIMEFRAME_H6, "h8": mt5.TIMEFRAME_H8, "h12": mt5.TIMEFRAME_H12,
      "d1": mt5.TIMEFRAME_D1, "w1": mt5.TIMEFRAME_W1, "mn1": mt5.TIMEFRAME_MN1}
PEND = {"bl": mt5.ORDER_TYPE_BUY_LIMIT, "sl": mt5.ORDER_TYPE_SELL_LIMIT,
        "bs": mt5.ORDER_TYPE_BUY_STOP, "ss": mt5.ORDER_TYPE_SELL_STOP}
PEND_BACK = {v: (k, "B" if k[0] == "b" else "S") for k, v in PEND.items()}


def offset():
    if UTC_OFFSET_H is not None:
        return int(float(UTC_OFFSET_H) * 3600)
    for s in ("EURUSD", "XAUUSD", "BTCUSD"):  # تشخیص تقریبی؛ اگر بازار بسته است MT5_UTC_OFFSET را بدهید
        t = mt5.symbol_info_tick(s)
        if t:
            d = t.time - time.time()
            if abs(d - round(d / 3600) * 3600) < 120 and abs(d) < 15 * 3600:
                return int(round(d / 3600) * 3600)
    return 0


@app.before_request
def auth():
    if request.method == "OPTIONS":
        return None
    if not hmac.compare_digest(request.headers.get("X-Token", ""), TOKEN):
        return jsonify(error="unauthorized"), 401


HINTS = {
    10027: "معاملات الگوریتمی (AutoTrading) در MT5 روی VPS خاموش است: دکمه Algo Trading را روشن کنید و در Tools ← Options ← Expert Advisors گزینه Allow algorithmic trading را تیک بزنید",
    10019: "موجودی یا مارجین کافی نیست", 10018: "بازار بسته است", 10017: "معامله برای این نماد غیرفعال است",
    10014: "حجم نامعتبر است", 10015: "قیمت نامعتبر است", 10016: "SL یا TP نامعتبر یا خیلی نزدیک به قیمت است",
    10030: "نوع پر شدن (filling) پذیرفته نشد", 10004: "قیمت تغییر کرد؛ دوباره تلاش کنید",
    10031: "اتصال MT5 به سرور بروکر برقرار نیست", 10024: "درخواست‌ها زیاد است؛ کمی صبر کنید",
}


def guard():
    a = mt5.account_info()
    if a is None:
        return "MT5 آماده نیست"
    ti = mt5.terminal_info()
    if ti is not None and not ti.trade_allowed:
        return HINTS[10027]
    if a.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO and not ALLOW_REAL:
        return "حساب واقعی مسدود است (ALLOW_REAL=1 لازم است)"
    return None


def done(r):
    if r is None:
        return jsonify(error=str(mt5.last_error())), 502
    if r.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
        return jsonify(error=f"{r.retcode}: {HINTS.get(r.retcode, '')} ({r.comment})"), 400
    return jsonify(ticket=r.order or r.deal, comment=r.comment)


@app.get("/api/ping")
def ping():
    a = mt5.account_info()
    if a is None:
        return jsonify(error="MT5 آماده نیست"), 503
    return jsonify(login=a.login, server=a.server, demo=a.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO,
                   balance=a.balance, equity=a.equity, currency=a.currency, allow_real=ALLOW_REAL, max_lot=MAX_LOT,
                   algo=bool(getattr(mt5.terminal_info(), 'trade_allowed', True)))


@app.get("/api/candles")
def candles():
    sym = request.args["symbol"]
    tf = TF.get(request.args.get("tf", "m15"))
    n = min(int(request.args.get("n", "5000")), 50000)
    if tf is None:
        return jsonify(error="تایم‌فریم نامعتبر"), 400
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, tf, 0, n)
    info = mt5.symbol_info(sym)
    if r is None or len(r) == 0 or info is None:
        return jsonify(error=f"دیتا برای {sym} پیدا نشد"), 404
    d, off = info.digits, offset()
    rows = [[int(x["time"]) - off, round(float(x["open"]), d), round(float(x["high"]), d),
             round(float(x["low"]), d), round(float(x["close"]), d)] for x in r]
    return jsonify(digits=d, rows=rows)


@app.get("/api/tick")
def tick():
    sym = request.args["symbol"]
    mt5.symbol_select(sym, True)
    t = mt5.symbol_info_tick(sym)
    if t is None:
        return jsonify(error="no tick"), 404
    return jsonify(bid=t.bid, ask=t.ask, time=t.time - offset())


@app.get("/api/positions")
def positions():
    ps = [dict(ticket=p.ticket, symbol=p.symbol, side="B" if p.type == mt5.POSITION_TYPE_BUY else "S",
               lots=p.volume, price=p.price_open, sl=p.sl, tp=p.tp, profit=p.profit, current=p.price_current)
          for p in (mt5.positions_get() or [])]
    os_ = [dict(ticket=o.ticket, symbol=o.symbol, type=PEND_BACK[o.type][0], side=PEND_BACK[o.type][1],
                lots=o.volume_current, price=o.price_open, sl=o.sl, tp=o.tp)
           for o in (mt5.orders_get() or []) if o.type in PEND_BACK]
    a = mt5.account_info()
    return jsonify(positions=ps, orders=os_, balance=a.balance, equity=a.equity, profit=a.profit, free=a.margin_free)


def fill_mode(sym, pending):
    if pending:
        return mt5.ORDER_FILLING_RETURN
    fm = mt5.symbol_info(sym).filling_mode
    if fm & 2:
        return mt5.ORDER_FILLING_IOC
    if fm & 1:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


@app.post("/api/order")
def order():
    g = guard()
    if g:
        return jsonify(error=g), 403
    d = request.get_json(force=True)
    sym, side, typ = d["symbol"], d["side"], d.get("type", "mkt")
    lots, sl, tp = float(d["lots"]), float(d.get("sl") or 0), float(d.get("tp") or 0)
    info = mt5.symbol_info(sym)
    if info is None:
        return jsonify(error="نماد پیدا نشد"), 404
    mt5.symbol_select(sym, True)
    if sl <= 0:
        return jsonify(error="حد ضرر (SL) الزامی است"), 400
    if lots > MAX_LOT:
        return jsonify(error=f"حجم بیشتر از سقف MAX_LOT={MAX_LOT} است"), 400
    step = info.volume_step
    lots = round(round(lots / step) * step, 2)
    if lots < info.volume_min or lots > info.volume_max:
        return jsonify(error="حجم نامعتبر"), 400
    t = mt5.symbol_info_tick(sym)
    pend = typ != "mkt"
    if pend:
        if typ not in PEND:
            return jsonify(error="نوع سفارش نامعتبر"), 400
        price, otype, action = float(d["price"]), PEND[typ], mt5.TRADE_ACTION_PENDING
    else:
        otype = mt5.ORDER_TYPE_BUY if side == "B" else mt5.ORDER_TYPE_SELL
        price, action = (t.ask if side == "B" else t.bid), mt5.TRADE_ACTION_DEAL
    if (side == "B" and sl >= price) or (side == "S" and sl <= price):
        return jsonify(error="SL در سمت اشتباه است"), 400
    if tp and ((side == "B" and tp <= price) or (side == "S" and tp >= price)):
        return jsonify(error="TP در سمت اشتباه است"), 400
    req = dict(action=action, symbol=sym, volume=lots, type=otype, price=price, sl=sl, tp=tp, deviation=20,
               magic=MAGIC, comment="fxapp", type_time=mt5.ORDER_TIME_GTC, type_filling=fill_mode(sym, pend))
    with lock:
        return done(mt5.order_send(req))


@app.post("/api/modify")
def modify():
    g = guard()
    if g:
        return jsonify(error=g), 403
    d = request.get_json(force=True)
    p = mt5.positions_get(ticket=int(d["ticket"]))
    if not p:
        return jsonify(error="معامله پیدا نشد"), 404
    sl, tp = float(d.get("sl") or 0), float(d.get("tp") or 0)
    if sl <= 0:
        return jsonify(error="حد ضرر (SL) الزامی است"), 400
    req = dict(action=mt5.TRADE_ACTION_SLTP, position=p[0].ticket, symbol=p[0].symbol, sl=sl, tp=tp)
    with lock:
        return done(mt5.order_send(req))


@app.post("/api/close")
def close():
    g = guard()
    if g:
        return jsonify(error=g), 403
    d = request.get_json(force=True)
    p = mt5.positions_get(ticket=int(d["ticket"]))
    if not p:
        return jsonify(error="معامله پیدا نشد"), 404
    p = p[0]
    info = mt5.symbol_info(p.symbol)
    vol = min(float(d.get("volume") or p.volume), p.volume)
    vol = round(round(vol / info.volume_step) * info.volume_step, 2)
    if vol < info.volume_min:
        return jsonify(error="حجم نامعتبر"), 400
    t = mt5.symbol_info_tick(p.symbol)
    buy = p.type == mt5.POSITION_TYPE_BUY
    req = dict(action=mt5.TRADE_ACTION_DEAL, symbol=p.symbol, volume=vol, position=p.ticket,
               type=mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY, price=t.bid if buy else t.ask,
               deviation=20, magic=MAGIC, comment="fxapp close", type_time=mt5.ORDER_TIME_GTC,
               type_filling=fill_mode(p.symbol, False))
    with lock:
        return done(mt5.order_send(req))


@app.get("/api/symbols")
def symbols():
    # نمادهای روشن در Market Watch
    allv = request.args.get("all") == "1"
    return jsonify([x.name for x in (mt5.symbols_get() or []) if allv or x.visible])


@app.get("/api/symbol")
def symbol():
    i = mt5.symbol_info(request.args["symbol"])
    if i is None:
        return jsonify(error="نماد پیدا نشد"), 404
    return jsonify(tick_value=i.trade_tick_value, tick_size=i.trade_tick_size, digits=i.digits,
                   spread=i.spread, volume_min=i.volume_min, volume_step=i.volume_step)


@app.get("/api/watch")
def watch():
    out = []
    for s in request.args.get("symbols", "").split(","):
        if not s:
            continue
        mt5.symbol_select(s, True)
        tk, i = mt5.symbol_info_tick(s), mt5.symbol_info(s)
        if tk is None or i is None:
            continue
        out.append(dict(symbol=s, bid=tk.bid, ask=tk.ask, chg=getattr(i, "price_change", 0)))
    return jsonify(out)


@app.get("/api/margin")
def margin():
    sym, side, lots = request.args["symbol"], request.args.get("side", "B"), float(request.args["lots"])
    t = mt5.symbol_info_tick(sym)
    if t is None:
        return jsonify(error="no tick"), 404
    buy = side == "B"
    m = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL, sym, lots, t.ask if buy else t.bid)
    if m is None:
        return jsonify(error=str(mt5.last_error())), 400
    return jsonify(margin=m, free=mt5.account_info().margin_free)


@app.post("/api/cancel")
def cancel():
    g = guard()
    if g:
        return jsonify(error=g), 403
    req = dict(action=mt5.TRADE_ACTION_REMOVE, order=int(request.get_json(force=True)["ticket"]))
    with lock:
        return done(mt5.order_send(req))


if __name__ == "__main__":
    if not mt5.initialize():
        raise SystemExit(f"اتصال به MT5 ناموفق بود: {mt5.last_error()} (برنامه MT5 باید باز و لاگین باشد)")
    a = mt5.account_info()
    print("حساب:", a.login, a.server, "DEMO" if a.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "REAL")
    print("سقف حجم:", MAX_LOT, "| حساب واقعی مجاز:", ALLOW_REAL)
    from waitress import serve
    serve(app, host="127.0.0.1", port=PORT)
