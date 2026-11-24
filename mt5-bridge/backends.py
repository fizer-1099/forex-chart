"""لایه اتصال پل به پلتفرم معاملاتی.

- MT5: با کتابخانه رسمی MetaTrader5 (مثل قبل).
- MT4: متاتریدر ۴ کتابخانه پایتون ندارد؛ پس اکسپرت FxBridge.mq4 داخل MT4 اجرا می‌شود،
  هر ~۲۵۰ میلی‌ثانیه به پل وصل می‌شود (WebRequest)، وضعیت حساب و قیمت‌ها را می‌فرستد و دستورها
  (دریافت کندل، سفارش، بستن، ...) را می‌گیرد و اجرا می‌کند.
"""
import re
import time
import threading
import itertools
from datetime import datetime


class BErr(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.msg, self.code = msg, code


TFSEC = {"m1": 60, "m2": 120, "m3": 180, "m4": 240, "m5": 300, "m6": 360, "m10": 600, "m12": 720, "m15": 900,
         "m20": 1200, "m30": 1800, "h1": 3600, "h2": 7200, "h3": 10800, "h4": 14400, "h6": 21600, "h8": 28800,
         "h12": 43200, "d1": 86400, "w1": 604800}
MT4_TF_MIN = {"m1": 1, "m5": 5, "m15": 15, "m30": 30, "h1": 60, "h4": 240, "d1": 1440, "w1": 10080, "mn1": 43200}

HINTS = {
    10027: "معاملات الگوریتمی (AutoTrading) در MT5 روی VPS خاموش است: دکمه Algo Trading را روشن کنید و در Tools ← Options ← Expert Advisors گزینه Allow algorithmic trading را تیک بزنید",
    10019: "موجودی یا مارجین کافی نیست", 10018: "بازار بسته است", 10017: "معامله برای این نماد غیرفعال است",
    10014: "حجم نامعتبر است", 10015: "قیمت نامعتبر است", 10016: "SL یا TP نامعتبر یا خیلی نزدیک به قیمت است",
    10030: "نوع پر شدن (filling) پذیرفته نشد", 10004: "قیمت تغییر کرد؛ دوباره تلاش کنید",
    10031: "اتصال MT5 به سرور بروکر برقرار نیست", 10024: "درخواست‌ها زیاد است؛ کمی صبر کنید",
}
MT4_ALGO_HINT = ("معاملات الگوریتمی در MT4 خاموش است: دکمه AutoTrading (بالای MT4) را روشن کنید و هنگام اجرای اکسپرت "
                 "FxBridge تیک Allow live trading را بزنید")
MT4_HINTS = {
    2: "خطای عمومی", 3: "پارامتر نامعتبر", 4: "سرور بروکر مشغول است", 6: "اتصال MT4 به سرور بروکر برقرار نیست",
    8: "درخواست‌ها زیاد است", 64: "حساب مسدود است", 65: "شماره حساب نامعتبر است", 128: "زمان انجام معامله تمام شد",
    129: "قیمت نامعتبر است", 130: "SL یا TP نامعتبر یا خیلی نزدیک به قیمت است", 131: "حجم نامعتبر است",
    132: "بازار بسته است", 133: "معامله برای این نماد غیرفعال است", 134: "موجودی یا مارجین کافی نیست",
    135: "قیمت تغییر کرد؛ دوباره تلاش کنید", 136: "قیمتی از بروکر نیامد (Off quotes)", 137: "بروکر مشغول است",
    138: "ریکوت؛ قیمت جدید آمد", 139: "سفارش قفل است", 141: "درخواست‌ها زیاد است", 145: "ویرایش رد شد: سفارش خیلی نزدیک به قیمت است",
    146: "بخش معامله MT4 مشغول است؛ دوباره تلاش کنید", 147: "تاریخ انقضا مجاز نیست", 148: "تعداد سفارش‌ها به سقف بروکر رسیده",
    149: "قفل (Hedge) روی این حساب مجاز نیست", 150: "طبق قانون FIFO ابتدا باید معامله‌های قدیمی‌تر بسته شوند",
    4051: "حجم نامعتبر است", 4106: "نماد پیدا نشد", 4108: "معامله یا سفارش پیدا نشد",
    4109: "معامله برای اکسپرت مجاز نیست: دکمه AutoTrading و تیک Allow live trading را روشن کنید",
    4110: "خرید (Long) مجاز نیست", 4111: "فروش (Short) مجاز نیست",
}

_SYM_RE = re.compile(r"[A-Za-z0-9._#\-+]{1,32}")


def _sym(s):
    if not isinstance(s, str) or not _SYM_RE.fullmatch(s):
        raise BErr("نام نماد نامعتبر", 400)
    return s


def _f(x):
    return f"{float(x):.8f}"


def make_backend(kind, utc_offset_h):
    kind = (kind or "mt5").lower()
    if kind == "mt4":
        return Mt4Backend(utc_offset_h)
    if kind == "mt5":
        return Mt5Backend(utc_offset_h)
    raise SystemExit("MT_PLATFORM باید mt4 یا mt5 باشد")


# ============================ MT5 ============================
class Mt5Backend:
    name = "MT5"
    algo_hint = HINTS[10027]

    def __init__(self, off_h):
        import MetaTrader5 as mt5
        self.m = mt5
        self.off_h = off_h
        self.TF = {"m1": mt5.TIMEFRAME_M1, "m2": mt5.TIMEFRAME_M2, "m3": mt5.TIMEFRAME_M3, "m4": mt5.TIMEFRAME_M4,
                   "m5": mt5.TIMEFRAME_M5, "m6": mt5.TIMEFRAME_M6, "m10": mt5.TIMEFRAME_M10, "m12": mt5.TIMEFRAME_M12,
                   "m15": mt5.TIMEFRAME_M15, "m20": mt5.TIMEFRAME_M20, "m30": mt5.TIMEFRAME_M30,
                   "h1": mt5.TIMEFRAME_H1, "h2": mt5.TIMEFRAME_H2, "h3": mt5.TIMEFRAME_H3, "h4": mt5.TIMEFRAME_H4,
                   "h6": mt5.TIMEFRAME_H6, "h8": mt5.TIMEFRAME_H8, "h12": mt5.TIMEFRAME_H12,
                   "d1": mt5.TIMEFRAME_D1, "w1": mt5.TIMEFRAME_W1, "mn1": mt5.TIMEFRAME_MN1}
        self.PEND = {"bl": mt5.ORDER_TYPE_BUY_LIMIT, "sl": mt5.ORDER_TYPE_SELL_LIMIT,
                     "bs": mt5.ORDER_TYPE_BUY_STOP, "ss": mt5.ORDER_TYPE_SELL_STOP}
        self.PEND_BACK = {v: (k, "B" if k[0] == "b" else "S") for k, v in self.PEND.items()}
        self.lock = threading.Lock()

    def start(self):
        m = self.m
        self._try = time.time()
        if not m.initialize():
            print(f"MT5: هنوز وصل نشد ({m.last_error()}). برنامه MT5 باید باز و لاگین باشد؛ بعداً خودکار دوباره تلاش می‌شود.")
            return
        a = m.account_info()
        print("پلتفرم: MT5 | حساب:", a.login, a.server, "DEMO" if a.trade_mode == m.ACCOUNT_TRADE_MODE_DEMO else "REAL")

    def offset(self):
        m = self.m
        if self.off_h is not None:
            return int(float(self.off_h) * 3600)
        for s in ("EURUSD", "XAUUSD", "BTCUSD"):  # تشخیص تقریبی؛ اگر بازار بسته است MT5_UTC_OFFSET را بدهید
            t = m.symbol_info_tick(s)
            if t:
                d = t.time - time.time()
                if abs(d - round(d / 3600) * 3600) < 120 and abs(d) < 15 * 3600:
                    return int(round(d / 3600) * 3600)
        return 0

    def account(self):
        m = self.m
        a = m.account_info()
        if a is None:
            if time.time() - getattr(self, "_try", 0) > 10:  # MT5 بعد از پل باز شده باشد، دوباره وصل شو
                self._try = time.time()
                try:
                    m.initialize()
                    a = m.account_info()
                except Exception:
                    a = None
            if a is None:
                return None
        ti = m.terminal_info()
        return dict(login=a.login, server=a.server, demo=a.trade_mode == m.ACCOUNT_TRADE_MODE_DEMO,
                    balance=a.balance, equity=a.equity, currency=a.currency, profit=a.profit, free=a.margin_free,
                    margin=a.margin, level=a.margin_level, algo=bool(getattr(ti, "trade_allowed", True)))

    def _done(self, r):
        m = self.m
        if r is None:
            raise BErr(str(m.last_error()), 502)
        if r.retcode not in (m.TRADE_RETCODE_DONE, m.TRADE_RETCODE_PLACED):
            raise BErr(f"{r.retcode}: {HINTS.get(r.retcode, '')} ({r.comment})", 400)
        return dict(ticket=r.order or r.deal, comment=r.comment)

    def candles(self, sym, tf, n):
        m = self.m
        t = self.TF.get(tf)
        if t is None:
            raise BErr("تایم‌فریم نامعتبر", 400)
        m.symbol_select(sym, True)
        r = m.copy_rates_from_pos(sym, t, 0, n)
        info = m.symbol_info(sym)
        if r is None or len(r) == 0 or info is None:
            raise BErr(f"دیتا برای {sym} پیدا نشد", 404)
        d, off = info.digits, self.offset()
        return d, [[int(x["time"]) - off, round(float(x["open"]), d), round(float(x["high"]), d),
                    round(float(x["low"]), d), round(float(x["close"]), d)] for x in r]

    def tick(self, sym):
        m = self.m
        m.symbol_select(sym, True)
        t = m.symbol_info_tick(sym)
        if t is None:
            return None
        return dict(bid=t.bid, ask=t.ask, time=t.time - self.offset())

    def positions(self):
        m, off = self.m, self.offset()
        ps = [dict(ticket=p.ticket, symbol=p.symbol, side="B" if p.type == m.POSITION_TYPE_BUY else "S",
                   lots=p.volume, price=p.price_open, sl=p.sl, tp=p.tp, profit=p.profit, current=p.price_current,
                   swap=p.swap, time=p.time - off) for p in (m.positions_get() or [])]
        os_ = [dict(ticket=o.ticket, symbol=o.symbol, type=self.PEND_BACK[o.type][0], side=self.PEND_BACK[o.type][1],
                    lots=o.volume_current, price=o.price_open, sl=o.sl, tp=o.tp)
               for o in (m.orders_get() or []) if o.type in self.PEND_BACK]
        return ps, os_

    def symbol_info(self, sym):
        i = self.m.symbol_info(sym)
        if i is None:
            return None
        return dict(tick_value=i.trade_tick_value, tick_size=i.trade_tick_size, digits=i.digits, spread=i.spread,
                    volume_min=i.volume_min, volume_step=i.volume_step, volume_max=i.volume_max)

    def _fill(self, sym, pending):
        m = self.m
        if pending:
            return m.ORDER_FILLING_RETURN
        fm = m.symbol_info(sym).filling_mode
        if fm & 2:
            return m.ORDER_FILLING_IOC
        if fm & 1:
            return m.ORDER_FILLING_FOK
        return m.ORDER_FILLING_RETURN

    def order(self, sym, side, typ, lots, price, sl, tp):
        m = self.m
        m.symbol_select(sym, True)
        pend = typ != "mkt"
        if pend:
            otype, action = self.PEND[typ], m.TRADE_ACTION_PENDING
        else:
            t = m.symbol_info_tick(sym)
            otype = m.ORDER_TYPE_BUY if side == "B" else m.ORDER_TYPE_SELL
            price, action = (t.ask if side == "B" else t.bid), m.TRADE_ACTION_DEAL
        req = dict(action=action, symbol=sym, volume=lots, type=otype, price=price, sl=sl, tp=tp, deviation=20,
                   magic=777, comment="fxapp", type_time=m.ORDER_TIME_GTC, type_filling=self._fill(sym, pend))
        with self.lock:
            return self._done(m.order_send(req))

    def modify(self, ticket, sl, tp):
        m = self.m
        p = m.positions_get(ticket=int(ticket))
        if not p:
            raise BErr("معامله پیدا نشد", 404)
        req = dict(action=m.TRADE_ACTION_SLTP, position=p[0].ticket, symbol=p[0].symbol, sl=sl, tp=tp)
        with self.lock:
            return self._done(m.order_send(req))

    def modify_order(self, ticket, price, sl, tp):
        m = self.m
        o = m.orders_get(ticket=int(ticket))
        if not o:
            raise BErr("سفارش پیدا نشد", 404)
        req = dict(action=m.TRADE_ACTION_MODIFY, order=o[0].ticket, price=price, sl=sl, tp=tp, type_time=m.ORDER_TIME_GTC)
        with self.lock:
            return self._done(m.order_send(req))

    def close(self, ticket, volume=None, guard=False):
        m = self.m
        p = m.positions_get(ticket=int(ticket))
        if not p:
            raise BErr("معامله پیدا نشد", 404)
        p = p[0]
        info = m.symbol_info(p.symbol)
        vol = min(float(volume or p.volume), p.volume)
        vol = round(round(vol / info.volume_step) * info.volume_step, 2)
        if vol < info.volume_min:
            raise BErr("حجم نامعتبر", 400)
        t = m.symbol_info_tick(p.symbol)
        buy = p.type == m.POSITION_TYPE_BUY
        req = dict(action=m.TRADE_ACTION_DEAL, symbol=p.symbol, volume=vol, position=p.ticket,
                   type=m.ORDER_TYPE_SELL if buy else m.ORDER_TYPE_BUY, price=t.bid if buy else t.ask,
                   deviation=30 if guard else 20, magic=777, comment="fxapp guard" if guard else "fxapp close",
                   type_time=m.ORDER_TIME_GTC, type_filling=self._fill(p.symbol, False))
        with self.lock:
            return self._done(m.order_send(req))

    def cancel(self, ticket):
        m = self.m
        with self.lock:
            return self._done(m.order_send(dict(action=m.TRADE_ACTION_REMOVE, order=int(ticket))))

    def symbols(self, allv):
        return [x.name for x in (self.m.symbols_get() or []) if allv or x.visible]

    def watch(self, symbols):
        m, out = self.m, []
        for s in symbols:
            m.symbol_select(s, True)
            tk, i = m.symbol_info_tick(s), m.symbol_info(s)
            if tk is None or i is None:
                continue
            out.append(dict(symbol=s, bid=tk.bid, ask=tk.ask, chg=getattr(i, "price_change", 0)))
        return out

    def history(self, t0, t1):
        m, off = self.m, self.offset()
        deals = m.history_deals_get(datetime.utcfromtimestamp(t0 - 2 * 86400), datetime.utcfromtimestamp(t1 + 2 * 86400))
        out = []
        for d in (deals or []):
            ts = d.time - off
            if ts < t0 or ts > t1 or d.type not in (m.DEAL_TYPE_BUY, m.DEAL_TYPE_SELL):
                continue
            out.append(dict(ticket=d.ticket, pos=d.position_id, time=ts, symbol=d.symbol,
                            side="B" if d.type == m.DEAL_TYPE_BUY else "S", entry=d.entry, lots=d.volume,
                            price=d.price, profit=d.profit, comm=d.commission, swap=d.swap, fee=getattr(d, "fee", 0)))
        return out

    def margin(self, sym, side, lots):
        m = self.m
        t = m.symbol_info_tick(sym)
        if t is None:
            raise BErr("no tick", 404)
        buy = side == "B"
        v = m.order_calc_margin(m.ORDER_TYPE_BUY if buy else m.ORDER_TYPE_SELL, sym, lots, t.ask if buy else t.bid)
        if v is None:
            raise BErr(str(m.last_error()), 400)
        return v, m.account_info().margin_free


# ============================ MT4 ============================
class Mt4Backend:
    """وضعیت را از اکسپرت می‌گیرد (/ea/poll) و دستورها را در صف می‌گذارد تا اکسپرت اجرا کند."""
    name = "MT4"
    algo_hint = MT4_ALGO_HINT

    def __init__(self, off_h):
        self.off_h = off_h
        self.lk = threading.Lock()
        self.q, self.w = [], {}
        self.ids = itertools.count(1)
        self.st, self.st_t = {}, 0.0
        self.ticks, self.want, self.si = {}, {}, {}
        self.srv_off = 0

    def start(self):
        print("پلتفرم: MT4 | منتظر اتصال اکسپرت FxBridge از داخل MT4 ... (روی یک چارت اجرایش کنید)")

    # ---------- ارتباط با اکسپرت ----------
    def online(self):
        return bool(self.st) and time.time() - self.st_t < 8

    def ea_poll(self, d):
        now = time.time()
        with self.lk:
            self.st, self.st_t = d, now
            off = int(d.get("off", 0) or 0)
            if abs(off) <= 14 * 3600:  # وقتی بازار بسته است اختلاف بی‌معنی می‌شود؛ نادیده بگیر
                self.srv_off = off
            for t in d.get("ticks", []):
                self.ticks[t[0]] = dict(bid=t[1], ask=t[2], time=t[3], chg=t[4], rx=now)
            for s in [s for s, v in self.want.items() if now - v > 120]:
                del self.want[s]
            out = ["W|" + ",".join(self.want)]
            while self.q:
                out.append(self.q.pop(0))
        return "\n".join(out)

    def ea_result(self, cid, last, body):
        with self.lk:
            w = self.w.get(cid)
            if not w:
                return
            w["parts"].append(body.replace("\x00", ""))
            if last:
                w["ev"].set()

    def _call(self, cmd, args, timeout=30):
        if not self.online():
            raise BErr("اکسپرت FxBridge به MT4 وصل نیست (MT4 باید باز باشد و اکسپرت روی یک چارت فعال باشد)", 503)
        cid = str(next(self.ids))
        w = dict(ev=threading.Event(), parts=[])
        with self.lk:
            self.w[cid] = w
            self.q.append("C|" + cid + "|" + cmd + "|" + "|".join(str(a) for a in args))
        ok = w["ev"].wait(timeout)
        with self.lk:
            self.w.pop(cid, None)
        if not ok:
            raise BErr("پاسخی از MT4 نیامد (زمان تمام شد)", 504)
        return self._ok("".join(w["parts"]))

    @staticmethod
    def _ok(text):
        lines = text.split("\n")
        head = lines[0].strip()
        if head.startswith("ERR"):
            p = head.split("|", 2)
            try:
                code = int(p[1])
            except Exception:
                code = 0
            msg = p[2] if len(p) > 2 else ""
            raise BErr(f"{code}: {MT4_HINTS.get(code, '')} ({msg})".replace(" ()", ""), 400)
        if head != "OK":
            raise BErr("پاسخ نامعتبر از اکسپرت", 502)
        return lines[1:]

    def offset(self):
        if self.off_h is not None:
            return int(float(self.off_h) * 3600)
        return self.srv_off

    def _watch(self, syms):
        now = time.time()
        with self.lk:
            for s in syms:
                self.want[s] = now
            if len(self.want) > 40:
                for s in sorted(self.want, key=self.want.get)[:len(self.want) - 40]:
                    del self.want[s]

    def _wait_ticks(self, syms, maxwait):
        end = time.time() + maxwait
        while time.time() < end:
            now = time.time()
            if all(s in self.ticks and now - self.ticks[s]["rx"] < 5 for s in syms):
                return
            time.sleep(0.05)

    # ---------- رابط یکسان با MT5 ----------
    def account(self):
        if not self.online():
            return None
        d = self.st
        try:
            return dict(login=d["login"], server=d["server"], demo=bool(d["demo"]), balance=d["bal"], equity=d["eq"],
                        currency=d["cur"], profit=d["prof"], free=d["free"], margin=d["margin"], level=d["lvl"],
                        algo=bool(d["algo"]))
        except KeyError:
            return None

    def _rates(self, sym, tfmin, n):
        sym = _sym(sym)
        n = max(1, min(int(n), 100000))
        lines = self._call("rates", [sym, tfmin, n], timeout=90)
        digits, off, rows = int(lines[0]), self.offset(), []
        for ln in lines[1:]:
            if not ln.strip():
                continue
            t, o, h, l, c = ln.strip().split(",")
            rows.append([int(t) - off, float(o), float(h), float(l), float(c)])
        if not rows:
            raise BErr(f"دیتا برای {sym} پیدا نشد", 404)
        return digits, rows

    def candles(self, sym, tf, n):
        if tf in MT4_TF_MIN:
            return self._rates(sym, MT4_TF_MIN[tf], n)
        sec = TFSEC.get(tf)
        if not sec:
            raise BErr("تایم‌فریم نامعتبر", 400)
        # تایم‌فریم‌هایی که MT4 ندارد (m2، m3، h2، h8 و...) از تایم‌فریم کوچک‌تر ساخته می‌شوند
        base = next(b for b in (240, 60, 30, 15, 5, 1) if sec % (b * 60) == 0)
        f = sec // (base * 60)
        digits, rows = self._rates(sym, base, n * f + f)
        off, out, cur = self.offset(), [], None
        for t, o, h, l, c in rows:
            k = ((t + off) // sec) * sec - off
            if cur and cur[0] == k:
                cur[2], cur[3], cur[4] = max(cur[2], h), min(cur[3], l), c
            else:
                cur = [k, o, h, l, c]
                out.append(cur)
        return digits, out[-n:]

    def tick(self, sym):
        sym = _sym(sym)
        if not self.online():
            raise BErr("اکسپرت FxBridge به MT4 وصل نیست", 503)
        self._watch([sym])
        self._wait_ticks([sym], 2.0)
        t = self.ticks.get(sym)
        if not t:
            return None
        return dict(bid=t["bid"], ask=t["ask"], time=t["time"] - self.offset())

    def positions(self):
        if not self.online():
            raise BErr("اکسپرت FxBridge به MT4 وصل نیست", 503)
        d, off = self.st, self.offset()
        ps = [dict(ticket=p[0], symbol=p[1], side="B" if p[2] == 0 else "S", lots=p[3], price=p[4], sl=p[5], tp=p[6],
                   profit=p[7], swap=p[8], time=p[9] - off, current=p[10]) for p in d.get("pos", [])]
        names = {2: "bl", 3: "sl", 4: "bs", 5: "ss"}
        os_ = [dict(ticket=o[0], symbol=o[1], type=names[o[2]], side="B" if o[2] in (2, 4) else "S", lots=o[3],
                    price=o[4], sl=o[5], tp=o[6]) for o in d.get("ord", []) if o[2] in names]
        return ps, os_

    def symbol_info(self, sym):
        sym = _sym(sym)
        h = self.si.get(sym)
        if h and time.time() - h[0] < 60:
            return h[1]
        try:
            lines = self._call("syminfo", [sym])
        except BErr as e:
            if e.code == 400:
                return None  # نماد ناشناخته
            raise
        v = lines[0].strip().split(",")
        info = dict(digits=int(float(v[0])), tick_value=float(v[1]), tick_size=float(v[2]), spread=float(v[3]),
                    volume_min=float(v[4]), volume_step=float(v[5]), volume_max=float(v[6]))
        self.si[sym] = (time.time(), info)
        return info

    def order(self, sym, side, typ, lots, price, sl, tp):
        sym = _sym(sym)
        code = {"mkt": 0 if side == "B" else 1, "bl": 2, "sl": 3, "bs": 4, "ss": 5}.get(typ)
        if code is None:
            raise BErr("نوع سفارش نامعتبر", 400)
        lines = self._call("order", [sym, code, _f(lots), _f(price or 0), _f(sl), _f(tp)], timeout=40)
        return dict(ticket=int(lines[0]), comment="")

    def modify(self, ticket, sl, tp):
        self._call("modify", [int(ticket), _f(sl), _f(tp)], timeout=30)
        return dict(ticket=int(ticket), comment="")

    def modify_order(self, ticket, price, sl, tp):
        self._call("modifyorder", [int(ticket), _f(price), _f(sl), _f(tp)], timeout=30)
        return dict(ticket=int(ticket), comment="")

    def close(self, ticket, volume=None, guard=False):
        ticket = int(ticket)
        p = next((p for p in self.st.get("pos", []) if p[0] == ticket), None)
        if not p:
            raise BErr("معامله پیدا نشد", 404)
        info = self.symbol_info(p[1]) or dict(volume_min=0.01, volume_step=0.01)
        vol = min(float(volume or p[3]), p[3])
        step = info["volume_step"] or 0.01
        vol = round(round(vol / step) * step, 3)
        if vol < info["volume_min"]:
            raise BErr("حجم نامعتبر", 400)
        self._call("close", [ticket, _f(vol)], timeout=40)
        return dict(ticket=ticket, comment="")

    def cancel(self, ticket):
        self._call("cancel", [int(ticket)], timeout=30)
        return dict(ticket=int(ticket), comment="")

    def symbols(self, allv):
        return [s.strip() for s in self._call("symbols", [1 if allv else 0], timeout=60) if s.strip()]

    def watch(self, symbols):
        syms = [_sym(s) for s in symbols if s]
        if not self.online():
            raise BErr("اکسپرت FxBridge به MT4 وصل نیست", 503)
        self._watch(syms)
        self._wait_ticks(syms, 1.5)
        return [dict(symbol=s, bid=self.ticks[s]["bid"], ask=self.ticks[s]["ask"], chg=self.ticks[s]["chg"])
                for s in syms if s in self.ticks]

    def history(self, t0, t1):
        off = self.offset()
        lines = self._call("history", [int(t0 + off - 86400), int(t1 + off + 86400)], timeout=60)
        out = []
        for ln in lines:
            v = ln.strip().split(",")
            if len(v) < 11:
                continue
            tk, sym, typ, lots = int(v[0]), v[1], int(v[2]), float(v[3])
            ot, op, ct, cp = int(v[4]) - off, float(v[5]), int(v[6]) - off, float(v[7])
            profit, comm, swap = float(v[8]), float(v[9]), float(v[10])
            if ct < t0 or ct > t1:
                continue
            side = "B" if typ == 0 else "S"
            out.append(dict(ticket=tk, pos=tk, time=ot, symbol=sym, side=side, entry=0, lots=lots, price=op,
                            profit=0.0, comm=0.0, swap=0.0, fee=0))
            out.append(dict(ticket=tk, pos=tk, time=ct, symbol=sym, side="S" if side == "B" else "B", entry=1,
                            lots=lots, price=cp, profit=profit, comm=comm, swap=swap, fee=0))
        return out

    def margin(self, sym, side, lots):
        sym = _sym(sym)
        v = self._call("margin", [sym], timeout=20)[0].strip().split(",")
        per_lot, free = float(v[0]), float(v[1])
        if per_lot <= 0:
            raise BErr("مارجین این نماد محاسبه نشد", 400)
        return per_lot * float(lots), free
