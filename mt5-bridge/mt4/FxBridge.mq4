//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
#property copyright "forex-chart"
#property version   "1.00"
#property strict

input string BridgeUrl   = "http://127.0.0.1";     // Bridge URL (must be allowed in Tools>Options>Expert Advisors)
input string BridgeToken = "";                      // Same value as BRIDGE_TOKEN in run.bat (min 24 chars)
input int    PollMs      = 250;                     // Poll interval (ms)
input int    Slippage    = 20;                      // Max slippage (points)
input int    MagicNumber = 777;                     // Magic number for app trades

string   g_want[];
int      g_wantN   = 0;
string   g_wantStr = "";
bool     g_busy    = false;
datetime g_lastOk  = 0;
datetime g_lastErr = 0;
int      g_fails   = 0;
int      g_lastE   = 0;
bool     g_useAlt  = false;
string   g_lastUrl = "";

//+------------------------------------------------------------------+
int OnInit()
  {
   if(StringLen(BridgeToken) < 24)
     {
      Alert("FxBridge: set BridgeToken in Inputs tab (same as BRIDGE_TOKEN in run.bat, min 24 chars)");
      return(INIT_PARAMETERS_INCORRECT);
     }
   EventSetMillisecondTimer(MathMax(100, PollMs));
   Comment("FxBridge: starting...");
   return(INIT_SUCCEEDED);
  }
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
   Comment("");
  }
//+------------------------------------------------------------------+
void OnTick() { }
//+------------------------------------------------------------------+
void OnTimer()
  {
   if(g_busy)
      return;
   g_busy = true;
   string resp = "";
   int code = Post("/ea/poll", BuildState(), resp, 5000);
   if(code == 200)
     {
      g_lastOk = TimeLocal();
      g_fails  = 0;
      Process(resp);
      Comment("FxBridge: connected | ", AccountServer(), " | ", (IsDemo() ? "DEMO" : "REAL"), " | ", TimeToString(TimeLocal(), TIME_SECONDS));
     }
   else
     {
      g_fails++;
      string why = "";
      if(code == -1 && g_lastE == 4060)
         why = "URL NOT ALLOWED: add " + BridgeUrl + " in Tools>Options>Expert Advisors, press OK, then remove and re-attach this EA";
      else if(code == -1 && (g_lastE == 5200 || g_lastE == 5201))
         why = "invalid URL [" + g_lastUrl + "] - MT4 accepts only port 80/443: use BridgeUrl=http://127.0.0.1 and run the bridge with EA_PORT=80";
      else if(code == -1)
         why = "cannot reach the bridge (run.bat not running? wrong port? firewall?)";
      else if(code == 401)
         why = "WRONG TOKEN: BridgeToken must equal BRIDGE_TOKEN in run.bat";
      else
         why = "bridge answered with HTTP " + IntegerToString(code);
      Comment("FxBridge: NOT connected | code ", code, " | MT4 error ", g_lastE, "\n", why);
     }
   g_busy = false;
  }
//+------------------------------------------------------------------+
//| HTTP                                                             |
//+------------------------------------------------------------------+
string BaseUrl()
  {
   string u = BridgeUrl;
   StringTrimLeft(u);
   StringTrimRight(u);
   while(StringLen(u) > 0 && StringGetCharacter(u, StringLen(u) - 1) == '/')
      u = StringSubstr(u, 0, StringLen(u) - 1);
   if(g_useAlt)
      StringReplace(u, "127.0.0.1", "localhost");
   return(u);
  }
//+------------------------------------------------------------------+
int PostOnce(string url, string body, string &resp, int timeout)
  {
   char data[], res[];
   string rh;
   string headers = "Content-Type: text/plain; charset=utf-8\r\nX-Token: " + BridgeToken + "\r\n";
   StringToCharArray(body, data, 0, StringLen(body), CP_UTF8);
   ResetLastError();
   int code = WebRequest("POST", url, headers, timeout, data, res, rh);
   if(code == -1)
     {
      g_lastE = GetLastError();
      return(-1);
     }
   resp = CharArrayToString(res, 0, WHOLE_ARRAY, CP_UTF8);
   return(code);
  }
//+------------------------------------------------------------------+
int Post(string path, string body, string &resp, int timeout)
  {
   if(StringLen(body) == 0)
      body = " ";
   g_lastUrl = BaseUrl() + path;
   int code = PostOnce(g_lastUrl, body, resp, timeout);
   if(code == -1 && g_lastE == 5200)
     {
      g_useAlt = !g_useAlt;          // try the other spelling (127.0.0.1 <-> localhost)
      g_lastUrl = BaseUrl() + path;
      code = PostOnce(g_lastUrl, body, resp, timeout);
      if(code == -1 && g_lastE == 5200)
         g_useAlt = !g_useAlt;
     }
   if(code == -1 && TimeLocal() - g_lastErr >= 30)
     {
      g_lastErr = TimeLocal();
      if(g_lastE == 4060)
         Print("FxBridge: URL not allowed. Add it in Tools > Options > Expert Advisors > Allow WebRequest for listed URL: ", g_lastUrl);
      else
         Print("FxBridge: WebRequest failed, MT4 error ", g_lastE, " url=[", g_lastUrl, "] len=", StringLen(g_lastUrl));
     }
   return(code);
  }
//+------------------------------------------------------------------+
void PostResult(string id, bool last, string body)
  {
   string resp = "";
   string path = "/ea/result?id=" + id + "&last=" + (last ? "1" : "0");
   for(int i = 0; i < 3; i++)
     {
      if(Post(path, body, resp, 30000) == 200)
         return;
      Sleep(200);
     }
  }
//+------------------------------------------------------------------+
void ReplyOk(string id, string extra = "")
  {
   PostResult(id, true, "OK\n" + extra);
  }
//+------------------------------------------------------------------+
void ReplyErr(string id, int code, string msg = "")
  {
   PostResult(id, true, "ERR|" + IntegerToString(code) + "|" + msg);
  }
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
string Esc(string s)
  {
   StringReplace(s, "\\", "\\\\");
   StringReplace(s, "\"", "\\\"");
   return(s);
  }
//+------------------------------------------------------------------+
string BuildState()
  {
   double eq = AccountEquity(), mg = AccountMargin();
   double lvl = (mg > 0) ? eq / mg * 100.0 : 0.0;
   long   d   = (long)(TimeCurrent() - TimeGMT());
   long   off = (long)(MathRound(d / 3600.0) * 3600.0);
   int    algo = (TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) && MQLInfoInteger(MQL_TRADE_ALLOWED)) ? 1 : 0;

   string s = "{";
   s += "\"login\":" + IntegerToString(AccountNumber());
   s += ",\"server\":\"" + Esc(AccountServer()) + "\"";
   s += ",\"demo\":" + (IsDemo() ? "true" : "false");
   s += ",\"bal\":" + DoubleToString(AccountBalance(), 2);
   s += ",\"eq\":" + DoubleToString(eq, 2);
   s += ",\"cur\":\"" + Esc(AccountCurrency()) + "\"";
   s += ",\"prof\":" + DoubleToString(AccountProfit(), 2);
   s += ",\"free\":" + DoubleToString(AccountFreeMargin(), 2);
   s += ",\"margin\":" + DoubleToString(mg, 2);
   s += ",\"lvl\":" + DoubleToString(lvl, 2);
   s += ",\"algo\":" + (algo == 1 ? "true" : "false");
   s += ",\"off\":" + IntegerToString(off);

   string ps = "", os = "";
   int total = OrdersTotal();
   for(int i = 0; i < total; i++)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      int    t   = OrderType();
      string sym = OrderSymbol();
      int    dg  = (int)MarketInfo(sym, MODE_DIGITS);
      if(t <= OP_SELL)
        {
         double cur = (t == OP_BUY) ? MarketInfo(sym, MODE_BID) : MarketInfo(sym, MODE_ASK);
         if(ps != "")
            ps += ",";
         ps += "[" + IntegerToString(OrderTicket()) + ",\"" + Esc(sym) + "\"," + IntegerToString(t) + "," +
               DoubleToString(OrderLots(), 3) + "," + DoubleToString(OrderOpenPrice(), dg) + "," +
               DoubleToString(OrderStopLoss(), dg) + "," + DoubleToString(OrderTakeProfit(), dg) + "," +
               DoubleToString(OrderProfit(), 2) + "," + DoubleToString(OrderSwap(), 2) + "," +
               IntegerToString((long)OrderOpenTime()) + "," + DoubleToString(cur, dg) + "]";
        }
      else if(t <= OP_SELLSTOP)
        {
         if(os != "")
            os += ",";
         os += "[" + IntegerToString(OrderTicket()) + ",\"" + Esc(sym) + "\"," + IntegerToString(t) + "," +
               DoubleToString(OrderLots(), 3) + "," + DoubleToString(OrderOpenPrice(), dg) + "," +
               DoubleToString(OrderStopLoss(), dg) + "," + DoubleToString(OrderTakeProfit(), dg) + "]";
        }
     }
   s += ",\"pos\":[" + ps + "],\"ord\":[" + os + "]";

   string ts = "";
   for(int k = 0; k < g_wantN; k++)
     {
      string sym = g_want[k];
      double b = MarketInfo(sym, MODE_BID), a = MarketInfo(sym, MODE_ASK);
      if(b <= 0)
         continue;
      int    dg = (int)MarketInfo(sym, MODE_DIGITS);
      double pc = iClose(sym, PERIOD_D1, 1);
      double chg = (pc > 0) ? (b / pc - 1.0) * 100.0 : 0.0;
      long   tm = (long)MarketInfo(sym, MODE_TIME);
      if(ts != "")
         ts += ",";
      ts += "[\"" + Esc(sym) + "\"," + DoubleToString(b, dg) + "," + DoubleToString(a, dg) + "," +
            IntegerToString(tm) + "," + DoubleToString(chg, 3) + "]";
     }
   s += ",\"ticks\":[" + ts + "]}";
   return(s);
  }
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
void Process(string resp)
  {
   string lines[];
   int n = StringSplit(resp, '\n', lines);
   for(int i = 0; i < n; i++)
     {
      string ln = lines[i];
      StringTrimLeft(ln);
      StringTrimRight(ln);
      if(StringLen(ln) < 2)
         continue;
      string p[];
      int k = StringSplit(ln, '|', p);
      if(k < 1)
         continue;
      if(p[0] == "W")
        {
         string w = (k >= 2) ? p[1] : "";
         if(w != g_wantStr)
           {
            g_wantStr = w;
            g_wantN = 0;
            if(StringLen(w) > 0)
              {
               string ws[];
               int m = StringSplit(w, ',', ws);
               ArrayResize(g_want, m);
               for(int j = 0; j < m; j++)
                 {
                  g_want[j] = ws[j];
                  SymbolSelect(ws[j], true);
                 }
               g_wantN = m;
              }
           }
        }
      else if(p[0] == "C" && k >= 3)
         Exec(p[1], p[2], p, k);
     }
  }
//+------------------------------------------------------------------+
void Exec(string id, string cmd, string &p[], int k)
  {
   if(cmd == "rates" && k >= 6)
      CmdRates(id, p[3], (int)StringToInteger(p[4]), (int)StringToInteger(p[5]));
   else if(cmd == "order" && k >= 9)
      CmdOrder(id, p[3], (int)StringToInteger(p[4]), StringToDouble(p[5]), StringToDouble(p[6]), StringToDouble(p[7]), StringToDouble(p[8]));
   else if(cmd == "modify" && k >= 6)
      CmdModify(id, (int)StringToInteger(p[3]), 0.0, StringToDouble(p[4]), StringToDouble(p[5]), false);
   else if(cmd == "modifyorder" && k >= 7)
      CmdModify(id, (int)StringToInteger(p[3]), StringToDouble(p[4]), StringToDouble(p[5]), StringToDouble(p[6]), true);
   else if(cmd == "close" && k >= 5)
      CmdClose(id, (int)StringToInteger(p[3]), StringToDouble(p[4]));
   else if(cmd == "cancel" && k >= 4)
      CmdCancel(id, (int)StringToInteger(p[3]));
   else if(cmd == "symbols" && k >= 4)
      CmdSymbols(id, (int)StringToInteger(p[3]));
   else if(cmd == "syminfo" && k >= 4)
      CmdSymInfo(id, p[3]);
   else if(cmd == "history" && k >= 5)
      CmdHistory(id, (datetime)StringToInteger(p[3]), (datetime)StringToInteger(p[4]));
   else if(cmd == "margin" && k >= 4)
      CmdMargin(id, p[3]);
   else
      ReplyErr(id, 3, "unknown command " + cmd);
  }
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
void CmdRates(string id, string sym, int tfmin, int n)
  {
   if(!SymbolSelect(sym, true))
     {
      ReplyErr(id, 4106, sym);
      return;
     }
   int bars = iBars(sym, tfmin);
   if(bars <= 0)
     {
      iTime(sym, tfmin, 0);
      Sleep(500);
      bars = iBars(sym, tfmin);
     }
   if(bars <= 0)
     {
      ReplyErr(id, 4066, "no history for " + sym);
      return;
     }
   if(n > bars)
      n = bars;
   if(n < 1)
      n = 1;
   int dg = (int)MarketInfo(sym, MODE_DIGITS);
   string buf = "OK\n" + IntegerToString(dg) + "\n";
   int cnt = 0;
   for(int i = n - 1; i >= 0; i--)
     {
      StringAdd(buf, IntegerToString((long)iTime(sym, tfmin, i)) + "," +
                DoubleToString(iOpen(sym, tfmin, i), dg) + "," + DoubleToString(iHigh(sym, tfmin, i), dg) + "," +
                DoubleToString(iLow(sym, tfmin, i), dg) + "," + DoubleToString(iClose(sym, tfmin, i), dg) + "\n");
      cnt++;
      if(cnt >= 2000 && i > 0)
        {
         PostResult(id, false, buf);
         buf = "";
         cnt = 0;
        }
     }
   PostResult(id, true, buf);
  }
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
void CmdOrder(string id, string sym, int type, double lots, double price, double sl, double tp)
  {
   if(!SymbolSelect(sym, true))
     {
      ReplyErr(id, 4106, sym);
      return;
     }
   int dg = (int)MarketInfo(sym, MODE_DIGITS);
   int ticket = -1, err = 0;
   for(int tr = 0; tr < 3; tr++)
     {
      RefreshRates();
      double px = price;
      if(type == OP_BUY)
         px = MarketInfo(sym, MODE_ASK);
      else if(type == OP_SELL)
         px = MarketInfo(sym, MODE_BID);
      ResetLastError();
      ticket = OrderSend(sym, type, lots, NormalizeDouble(px, dg), Slippage, NormalizeDouble(sl, dg),
                         NormalizeDouble(tp, dg), "fxapp", MagicNumber, 0, clrNONE);
      if(ticket >= 0)
         break;
      err = GetLastError();
      if(err == 135 || err == 136 || err == 138 || err == 146)
        {
         Sleep(300);
         continue;
        }
      break;
     }

   if(ticket < 0 && err == 130 && type <= OP_SELL && (sl > 0 || tp > 0))
     {
      double pt = MarketInfo(sym, MODE_POINT);
      double minD = MarketInfo(sym, MODE_STOPLEVEL) * pt;
      double px = (type == OP_BUY) ? MarketInfo(sym, MODE_ASK) : MarketInfo(sym, MODE_BID);
      bool farEnough = (sl <= 0 || MathAbs(px - sl) >= minD) && (tp <= 0 || MathAbs(px - tp) >= minD);
      if(farEnough)
        {
         ticket = OrderSend(sym, type, lots, NormalizeDouble(px, dg), Slippage, 0, 0, "fxapp", MagicNumber, 0, clrNONE);
         if(ticket >= 0)
           {
            bool ok = false;
            if(OrderSelect(ticket, SELECT_BY_TICKET))
               ok = OrderModify(ticket, OrderOpenPrice(), NormalizeDouble(sl, dg), NormalizeDouble(tp, dg), 0, clrNONE);
            if(!ok)
              {
               int me = GetLastError();
               double cpx = (type == OP_BUY) ? MarketInfo(sym, MODE_BID) : MarketInfo(sym, MODE_ASK);
               OrderClose(ticket, lots, NormalizeDouble(cpx, dg), Slippage, clrNONE);
               ReplyErr(id, me, "SL could not be set after open; position was closed");
               return;
              }
           }
         else
            err = GetLastError();
        }
     }

   if(ticket < 0)
     {
      ReplyErr(id, err, "OrderSend failed");
      return;
     }
   ReplyOk(id, IntegerToString(ticket));
  }
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
void CmdModify(string id, int ticket, double price, double sl, double tp, bool isPending)
  {
   if(!OrderSelect(ticket, SELECT_BY_TICKET) || OrderCloseTime() != 0)
     {
      ReplyErr(id, 4108, "ticket " + IntegerToString(ticket));
      return;
     }
   int dg = (int)MarketInfo(OrderSymbol(), MODE_DIGITS);
   double op = isPending ? price : OrderOpenPrice();
   ResetLastError();
   bool ok = OrderModify(ticket, NormalizeDouble(op, dg), NormalizeDouble(sl, dg), NormalizeDouble(tp, dg),
                         OrderExpiration(), clrNONE);
   int e = GetLastError();
   if(!ok && e != 1)
     {
      ReplyErr(id, e, "OrderModify failed");
      return;
     }
   ReplyOk(id, IntegerToString(ticket));
  }
//+------------------------------------------------------------------+
void CmdClose(string id, int ticket, double lots)
  {
   if(!OrderSelect(ticket, SELECT_BY_TICKET) || OrderCloseTime() != 0 || OrderType() > OP_SELL)
     {
      ReplyErr(id, 4108, "ticket " + IntegerToString(ticket));
      return;
     }
   string sym = OrderSymbol();
   int    typ = OrderType();
   int    dg  = (int)MarketInfo(sym, MODE_DIGITS);
   if(lots <= 0 || lots > OrderLots())
      lots = OrderLots();
   int err = 0;
   for(int tr = 0; tr < 3; tr++)
     {
      RefreshRates();
      double px = (typ == OP_BUY) ? MarketInfo(sym, MODE_BID) : MarketInfo(sym, MODE_ASK);
      ResetLastError();
      if(OrderClose(ticket, lots, NormalizeDouble(px, dg), Slippage, clrNONE))
        {
         ReplyOk(id, IntegerToString(ticket));
         return;
        }
      err = GetLastError();
      if(err == 135 || err == 136 || err == 138 || err == 146)
        {
         Sleep(300);
         OrderSelect(ticket, SELECT_BY_TICKET);
         continue;
        }
      break;
     }
   ReplyErr(id, err, "OrderClose failed");
  }
//+------------------------------------------------------------------+
void CmdCancel(string id, int ticket)
  {
   if(!OrderSelect(ticket, SELECT_BY_TICKET) || OrderCloseTime() != 0 || OrderType() <= OP_SELL)
     {
      ReplyErr(id, 4108, "ticket " + IntegerToString(ticket));
      return;
     }
   ResetLastError();
   if(OrderDelete(ticket, clrNONE))
      ReplyOk(id, IntegerToString(ticket));
   else
      ReplyErr(id, GetLastError(), "OrderDelete failed");
  }
//+------------------------------------------------------------------+
void CmdSymbols(string id, int all)
  {
   bool sel = (all == 0);
   int total = SymbolsTotal(sel);
   string buf = "OK\n";
   for(int i = 0; i < total; i++)
      StringAdd(buf, SymbolName(i, sel) + "\n");
   PostResult(id, true, buf);
  }
//+------------------------------------------------------------------+
void CmdSymInfo(string id, string sym)
  {
   if(!SymbolSelect(sym, true))
     {
      ReplyErr(id, 4106, sym);
      return;
     }
   string s = DoubleToString(MarketInfo(sym, MODE_DIGITS), 0) + "," + DoubleToString(MarketInfo(sym, MODE_TICKVALUE), 8) + "," +
              DoubleToString(MarketInfo(sym, MODE_TICKSIZE), 10) + "," + DoubleToString(MarketInfo(sym, MODE_SPREAD), 0) + "," +
              DoubleToString(MarketInfo(sym, MODE_MINLOT), 4) + "," + DoubleToString(MarketInfo(sym, MODE_LOTSTEP), 4) + "," +
              DoubleToString(MarketInfo(sym, MODE_MAXLOT), 2);
   ReplyOk(id, s);
  }
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
void CmdHistory(string id, datetime t0, datetime t1)
  {
   string buf = "OK\n";
   int total = OrdersHistoryTotal();
   for(int i = 0; i < total; i++)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_HISTORY))
         continue;
      int t = OrderType();
      if(t > OP_SELL)
         continue;
      datetime ct = OrderCloseTime();
      if(ct < t0 || ct > t1)
         continue;
      string sym = OrderSymbol();
      int dg = (int)MarketInfo(sym, MODE_DIGITS);
      StringAdd(buf, IntegerToString(OrderTicket()) + "," + sym + "," + IntegerToString(t) + "," +
                DoubleToString(OrderLots(), 3) + "," + IntegerToString((long)OrderOpenTime()) + "," +
                DoubleToString(OrderOpenPrice(), dg) + "," + IntegerToString((long)ct) + "," +
                DoubleToString(OrderClosePrice(), dg) + "," + DoubleToString(OrderProfit(), 2) + "," +
                DoubleToString(OrderCommission(), 2) + "," + DoubleToString(OrderSwap(), 2) + "\n");
     }
   PostResult(id, true, buf);
  }
//+------------------------------------------------------------------+
void CmdMargin(string id, string sym)
  {
   if(!SymbolSelect(sym, true))
     {
      ReplyErr(id, 4106, sym);
      return;
     }
   ReplyOk(id, DoubleToString(MarketInfo(sym, MODE_MARGINREQUIRED), 4) + "," + DoubleToString(AccountFreeMargin(), 2));
  }
//+------------------------------------------------------------------+
