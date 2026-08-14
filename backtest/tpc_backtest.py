"""
TPC Backtest - Optimized for speed
"""
import os, sys, time, json, csv
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import List, Optional, Dict, Tuple
import numpy as np
import pandas as pd
import requests
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
TF_HTF, TF_LTF = "1h", "15m"
INITIAL_CAPITAL = 10000.0
RISK_PCT = 0.01
MAX_POS = 3
COMM = 0.001
SLIP = 0.0005

# TPC Parameters
EMA50, EMA200 = 50, 200
RSI_P = 14
EMA21 = 21
ATR_P = 14
SWING_N = 3
RR = (1.5, 2.5, 4.0)
SL_BUF = 0.2
MIN_RD_ATR, MAX_RD_ATR = 0.5, 3.0
MIN_VOL, MAX_VOL = 0.001, 0.03
RSI_OB, RS_OS = 70, 30
COOL = 2
IMP_N = 3
IMP_BODY = 0.5
CHASE_ATR, CHASE_BODY = 2.0, 3.0
MONTHS = 3

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
os.makedirs(DIR, exist_ok=True)

def fetch(sym, tf, months):
    api = sym.replace("/","")
    print(f"  {sym} {tf}...", end="", flush=True)
    
    now = int(datetime.now(timezone.utc).timestamp()*1000)
    start = int((datetime.now(timezone.utc) - timedelta(days=months*30)).timestamp()*1000)
    
    tf_s = {"15m":900,"1h":3600}[tf]
    data = []
    cur = start
    
    while cur < now:
        url = f"https://api.binance.com/api/v3/klines?symbol={api}&interval={tf}&startTime={cur}&limit=1000"
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent":"Mozilla/5.0"})
            if r.status_code == 429:
                time.sleep(60); continue
            if r.status_code != 200:
                print(f" ERR {r.status_code}"); break
            k = r.json()
            if not k: break
            data.extend(k)
            cur = int(k[-1][0]) + tf_s*1000
            if len(k)<1000: break
            time.sleep(0.05)
        except Exception as e:
            print(f" ERR {e}"); break
    
    df = pd.DataFrame(data, columns=["t","o","h","l","c","v","x","x1","x2","x3","x4","x5"])
    df["timestamp"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    df = df.rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
    df = df[["timestamp","open","high","low","close","volume"]].drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    # Convert string columns to float
    for col in ["open","high","low","close","volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    print(f" {len(df)} candles")
    return df

def fetch_all():
    data = {}
    for s in SYMBOLS:
        print(f"\n{s}:")
        data[s] = {TF_HTF: fetch(s,TF_HTF,MONTHS), TF_LTF: fetch(s,TF_LTF,MONTHS)}
    return data

# Indicators
def ema(s,p): return s.ewm(span=p,adjust=False).mean()
def rsi_calc(s,p=14):
    d=s.diff(); g=d.where(d>0,0.0); l=(-d).where(d<0,0.0)
    ag=g.ewm(alpha=1.0/p,min_periods=p).mean()
    al=l.ewm(alpha=1.0/p,min_periods=p).mean()
    return 100-(100/(1+ag/al.replace(0,np.nan)))
def atr_calc(h,l,c,p=14):
    pc=c.shift(1); tr=pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1.0/p,min_periods=p).mean()

def prep_htf(df):
    df=df.copy(); df["ema50"]=ema(df["close"],EMA50); df["ema200"]=ema(df["close"],EMA200)
    df["rsi14"]=rsi_calc(df["close"],RSI_P); return df

def prep_ltf(df):
    df=df.copy(); df["ema21"]=ema(df["close"],EMA21); df["atr14"]=atr_calc(df["high"],df["low"],df["close"],ATR_P)
    df["rsi14"]=rsi_calc(df["close"],RSI_P); df["avg_vol"]=df["volume"].rolling(20).mean(); return df

def swings(df,n=SWING_N):
    l=len(df); sh=np.full(l,np.nan); sl=np.full(l,np.nan)
    H=df["high"].values; L=df["low"].values
    for i in range(n,l-n):
        wh=H[i-n:i+n+1]
        if H[i]==np.max(wh) and np.sum(wh==H[i])==1: sh[i]=H[i]
        wl=L[i-n:i+n+1]
        if L[i]==np.min(wl) and np.sum(wl==L[i])==1: sl[i]=L[i]
    df=df.copy(); df["sh"]=sh; df["sl"]=sl; return df

def structure(df):
    l=len(df); s=pd.Series("RANGE",index=df.index)
    sv=df["sh"].values; lv=df["sl"].values; lsh=psh=lsl=psl=None
    for i in range(l):
        if not np.isnan(sv[i]): psh=lsh; lsh=sv[i]
        if not np.isnan(lv[i]): psl=lsl; lsl=lv[i]
        if all(v is not None for v in [lsh,psh,lsl,psl]):
            if lsh>psh and lsl>psl: s.iloc[i]="BULLISH"
            elif lsh<psh and lsl<psl: s.iloc[i]="BEARISH"
    return s

def htf_trend(r):
    e5,e2,rs,c=r.get("ema50"),r.get("ema200"),r.get("rsi14"),r.get("close")
    if any(pd.isna(v) for v in [e5,e2,rs]): return "N"
    if e5>e2 and c>e5 and rs>50: return "B"
    if e5<e2 and c<e5 and rs<50: return "S"
    return "N"

def impulse(df,ei,d):
    if ei<IMP_N: return False
    a=df["atr14"].values; cnt=0
    for j in range(ei,max(ei-10,IMP_N-1),-1):
        b=abs(df["close"].iloc[j]-df["open"].iloc[j])
        if a[j]<=0 or pd.isna(a[j]): break
        bull=df["close"].iloc[j]>df["open"].iloc[j]
        if (d=="B" and bull) or (d=="S" and not bull):
            if b>=IMP_BODY*a[j]: cnt+=1
            else: break
        else: break
    return cnt>=IMP_N

def imp_range(df,ei,d):
    if ei<IMP_N: return None
    a=df["atr14"].values; cnt=0; s=ei
    for j in range(ei,max(ei-10,IMP_N-1),-1):
        b=abs(df["close"].iloc[j]-df["open"].iloc[j])
        if a[j]<=0 or pd.isna(a[j]): break
        bull=df["close"].iloc[j]>df["open"].iloc[j]
        if (d=="B" and bull) or (d=="S" and not bull):
            if b>=IMP_BODY*a[j]: cnt+=1; s=j
            else: break
        else: break
    return (s,ei) if cnt>=IMP_N else None

def pullback(df,i,d,ir):
    if i<1: return False,"n"
    c,lo,hi=df["close"].iloc[i],df["low"].iloc[i],df["high"].iloc[i]
    e21,rv=df["ema21"].iloc[i],df["rsi14"].iloc[i]
    av,cv=df["avg_vol"].iloc[i],df["volume"].iloc[i]
    if pd.isna(e21) or pd.isna(rv): return False,"n"
    rok=(40<=rv<=50) if d=="B" else (50<=rv<=60)
    if not rok:
        if hasattr(pullback, '_debug') and pullback._debug:
            print(f"    PB FAIL RSI: rv={rv:.2f} d={d} rok={rok}")
        return False,"n"
    if not pd.isna(av) and av>0 and cv>av:
        if hasattr(pullback, '_debug') and pullback._debug:
            print(f"    PB FAIL VOL: cv={cv:.2f} > av={av:.2f}")
        return False,"n"
    ed=abs(c-e21)/e21 if e21>0 else 1
    if d=="B" and (ed<0.005 or lo<=e21): return True,"e21"
    if d=="S" and (ed<0.005 or hi>=e21): return True,"e21"
    if ir:
        ih=df["high"].iloc[ir[0]:ir[1]+1].max(); il=df["low"].iloc[ir[0]:ir[1]+1].min()
        f=(ih+il)/2; fd=abs(c-f)/f if f>0 else 1
        if d=="B" and (fd<0.003 or lo<=f): return True,"f50"
        if d=="S" and (fd<0.003 or hi>=f): return True,"f50"
    if hasattr(pullback, '_debug') and pullback._debug:
        print(f"    PB FAIL ZONE: e21={e21:.2f} c={c:.2f} ed={ed:.4f} f50={f if ir else 'N/A'}")
    return False,"n"

def confirm(df,i,d):
    if i<1: return False,"n"
    o,c,h,l=df["open"].iloc[i],df["close"].iloc[i],df["high"].iloc[i],df["low"].iloc[i]
    po,pc,ph=df["open"].iloc[i-1],df["close"].iloc[i-1],df["high"].iloc[i-1]
    b=abs(c-o); r=h-l if h>l else 0.0001
    if d=="B":
        if c>o and pc<po and c>po and o<pc and b>abs(pc-po): return True,"eng"
        ls=min(o,c)-l
        if ls>2*b and b>0 and r>0 and (c-l)/r>0.66: return True,"pin"
        if c>ph and c>o: return True,"brk"
    else:
        if c<o and pc>po and c<po and o>pc and b>abs(pc-po): return True,"eng"
        us=h-max(o,c)
        if us>2*b and b>0 and r>0 and (c-l)/r<0.33: return True,"pin"
        pl=df["low"].iloc[i-1]
        if c<pl and c<o: return True,"brk"
    return False,"n"

def chasing(df,i,d):
    if i<3: return False
    a=df["atr14"].iloc[i]
    if pd.isna(a) or a<=0: return False
    m=df["close"].iloc[i]-df["close"].iloc[i-3] if d=="B" else df["close"].iloc[i-3]-df["close"].iloc[i]
    if m>CHASE_ATR*a: return True
    return abs(df["close"].iloc[i]-df["open"].iloc[i])>CHASE_BODY*a

@dataclass
class T:
    ts:object; sym:str; d:str; ep:float; sl:float; t1:float; t2:float; t3:float
    xp:float=0; xr:str=""; q:float=0; ra:float=0; pnl:float=0; pp:float=0
    rm:float=0; fee:float=0; sc:float=0; dur:str=""; ht:str=""; st:str=""
    imp:str=""; pb:str=""; cf:str=""; a15:float=0; r1h:float=0; r15:float=0
    slev:float=0; rd:float=0; rr:float=0

@dataclass
class OP:
    t:T; et:object; rq:float; t1h:bool=False; t2h:bool=False; t3h:bool=False

class BT:
    def __init__(self):
        self.eq=INITIAL_CAPITAL; self.pk=INITIAL_CAPITAL; self.mdd=0; self.mddu=0
        self.trades=[]; self.op={}; self.cd={}; self.bk={"htf":0,"st":0,"imp":0,"pb":0,"cf":0,"rsi":0,"vol":0,"ch":0,"mp":0,"sp":0,"co":0,"sl":0}
        self.ec=[]
    def dd(self):
        if self.eq>self.pk: self.pk=self.eq
        d=(self.pk-self.eq)/self.pk if self.pk>0 else 0
        if d>self.mdd: self.mdd=d; self.mddu=self.pk-self.eq
    def co(self,s,i):
        if len(self.op)>=MAX_POS: self.bk["mp"]+=1; return False
        if s in self.op: self.bk["sp"]+=1; return False
        if s in self.cd and i<self.cd[s]: self.bk["co"]+=1; return False
        return True
    def openp(self,t,i):
        self.trades.append(t); self.op[t.sym]=OP(t,t.ts,t.q)
        self.ec.append({"ts":t.ts,"eq":self.eq,"s":t.sym,"pnl":0})
    def closep(self,s,ep,r,i,ct):
        if s not in self.op: return
        p=self.op[s]; t=p.t; s2=ep*SLIP
        ae=ep-s2 if t.d=="B" else ep+s2
        pu=ae-t.ep if t.d=="B" else t.ep-ae
        gp=pu*p.rq; ef=p.rq*ae*COMM
        np=gp-t.fee-ef
        t.xp=ae; t.xr=r; t.pnl=np
        t.pp=(np/(p.rq*t.ep))*100 if t.ep>0 else 0
        t.rm=np/t.ra if t.ra>0 else 0; t.fee+=ef
        t.sc=p.rq*t.ep*SLIP+p.rq*s2; t.dur=str(ct-t.ts)
        self.eq+=np; self.dd()
        self.ec.append({"ts":ct,"eq":self.eq,"s":s,"pnl":np})
        self.cd[s]=i+COOL; del self.op[s]
    def exits(self,df,i,s,ct):
        if s not in self.op: return
        p=self.op[s]; t=p.t; hi,lo=df["high"].iloc[i],df["low"].iloc[i]
        sl,t1,t2,t3=t.sl,t.t1,t.t2,t.t3
        if t.d=="B": sh=lo<=sl; t1h=not p.t1h and hi>=t1; t2h=not p.t2h and hi>=t2; t3h=not p.t3h and hi>=t3
        else: sh=hi>=sl; t1h=not p.t1h and lo<=t1; t2h=not p.t2h and lo<=t2; t3h=not p.t3h and lo<=t3
        if sh and (t1h or t2h or t3h): self.closep(s,sl,"sl",i,ct); return
        if sh: self.closep(s,sl,"sl",i,ct); return
        if t3h:
            p.t3h=True; p.rq*=0.34
            if p.rq<=0: self.closep(s,t3,"tp3",i,ct)
            return
        if t2h:
            p.t2h=True; p.rq-=t.q*0.33
            if p.rq<=0: self.closep(s,t2,"tp2",i,ct)
            return
        if t1h:
            p.t1h=True; p.rq-=t.q*0.33
            if p.rq<=0: self.closep(s,t1,"tp1",i,ct)
    def proc(self,s,dh,dl,i):
        ct=dl["timestamp"].iloc[i]; self.exits(dl,i,s,ct)
        hm=dh["timestamp"]<=ct
        if not hm.any(): return
        hi=dh[hm].index[-1]
        if i<2: return
        di=i-1
        ht=htf_trend(dh.iloc[hi])
        if ht=="N": self.bk["htf"]+=1; return
        st=structure(dl).iloc[di]
        if st=="R" or (ht=="B" and st!="B") or (ht=="S" and st!="S"):
            self.bk["st"]+=1; return
        d="B" if ht=="B" else "S"
        if not impulse(dl,di,d): self.bk["imp"]+=1; return
        ir=imp_range(dl,di,d)
        pb,pz=pullback(dl,di,d,ir)
        if not pb: self.bk["pb"]+=1; return
        cv,ct2=confirm(dl,i,d)
        if not cv: self.bk["cf"]+=1; return
        r1h=dh["rsi14"].iloc[hi]
        if not pd.isna(r1h):
            if (d=="B" and r1h>RSI_OB) or (d=="S" and r1h<RS_OS):
                self.bk["rsi"]+=1; return
        av=dl["atr14"].iloc[di]; cp=dl["close"].iloc[di]
        if pd.isna(av) or av<=0 or cp<=0: self.bk["vol"]+=1; return
        v=av/cp
        if v<MIN_VOL or v>MAX_VOL: self.bk["vol"]+=1; return
        if chasing(dl,i,d): self.bk["ch"]+=1; return
        if not self.co(s,i): return
        ep=dl["open"].iloc[i]; ep*=(1+SLIP) if d=="B" else (1-SLIP)
        if d=="B":
            sv=dl["sl"].values[:di+1]; vs=sv[~np.isnan(sv)]
            if len(vs)==0: return
            slp=vs[-1]-SL_BUF*av
        else:
            sv=dl["sh"].values[:di+1]; vs=sv[~np.isnan(sv)]
            if len(vs)==0: return
            slp=vs[-1]+SL_BUF*av
        rd=abs(ep-slp)
        if rd<MIN_RD_ATR*av or rd>MAX_RD_ATR*av: self.bk["sl"]+=1; return
        t1=ep+RR[0]*rd if d=="B" else ep-RR[0]*rd
        t2=ep+RR[1]*rd if d=="B" else ep-RR[1]*rd
        t3=ep+RR[2]*rd if d=="B" else ep-RR[2]*rd
        ra=self.eq*RISK_PCT; q=ra/rd if rd>0 else 0
        q=min(q,self.eq/ep) if ep>0 else 0
        if q<=0: return
        ef=q*ep*COMM
        self.openp(T(ts=ct,sym=s,d=d,ep=ep,sl=slp,t1=t1,t2=t2,t3=t3,q=q,ra=ra,fee=ef,
            ht=ht,st=st,imp=f"{IMP_N}+",pb=pz,cf=ct2,a15=av,
            r1h=r1h if not pd.isna(r1h) else 0,
            r15=dl["rsi14"].iloc[di] if not pd.isna(dl["rsi14"].iloc[di]) else 0,
            slev=vs[-1],rd=rd,rr=RR[0]),i)

    def proc_precomp(self,s,dh,dl,st_series):
        for i in range(len(dl)):
            if i%3000==0: print(f"  {i}/{len(dl)}",flush=True)
            self._proc_one(s,dh,dl,i,st_series)
        if s in self.op: self.closep(s,dl["close"].iloc[-1],"eod",len(dl)-1,dl["timestamp"].iloc[-1])
        print(f"  done",flush=True)

    def _proc_one(self,s,dh,dl,i,st_series):
        ct=dl["timestamp"].iloc[i]; self.exits(dl,i,s,ct)
        hm=dh["timestamp"]<=ct
        if not hm.any(): return
        hi=dh[hm].index[-1]
        if i<2: return
        di=i-1
        ht=htf_trend(dh.iloc[hi])
        if ht=="N": self.bk["htf"]+=1; return
        st=st_series.iloc[di]
        if st=="R" or (ht=="B" and st!="B") or (ht=="S" and st!="S"):
            self.bk["st"]+=1; return
        d="B" if ht=="B" else "S"
        if not impulse(dl,di,d): self.bk["imp"]+=1; return
        ir=imp_range(dl,di,d)
        pb,pz=pullback(dl,di,d,ir)
        if not pb: self.bk["pb"]+=1; return
        cv,ct2=confirm(dl,i,d)
        if not cv: self.bk["cf"]+=1; return
        r1h=dh["rsi14"].iloc[hi]
        if not pd.isna(r1h):
            if (d=="B" and r1h>RSI_OB) or (d=="S" and r1h<RS_OS):
                self.bk["rsi"]+=1; return
        av=dl["atr14"].iloc[di]; cp=dl["close"].iloc[di]
        if pd.isna(av) or av<=0 or cp<=0: self.bk["vol"]+=1; return
        v=av/cp
        if v<MIN_VOL or v>MAX_VOL: self.bk["vol"]+=1; return
        if chasing(dl,i,d): self.bk["ch"]+=1; return
        if not self.co(s,i): return
        ep=dl["open"].iloc[i]; ep*=(1+SLIP) if d=="B" else (1-SLIP)
        if d=="B":
            sv=dl["sl"].values[:di+1]; vs=sv[~np.isnan(sv)]
            if len(vs)==0: return
            slp=vs[-1]-SL_BUF*av
        else:
            sv=dl["sh"].values[:di+1]; vs=sv[~np.isnan(sv)]
            if len(vs)==0: return
            slp=vs[-1]+SL_BUF*av
        rd=abs(ep-slp)
        if rd<MIN_RD_ATR*av or rd>MAX_RD_ATR*av: self.bk["sl"]+=1; return
        t1=ep+RR[0]*rd if d=="B" else ep-RR[0]*rd
        t2=ep+RR[1]*rd if d=="B" else ep-RR[1]*rd
        t3=ep+RR[2]*rd if d=="B" else ep-RR[2]*rd
        ra=self.eq*RISK_PCT; q=ra/rd if rd>0 else 0
        q=min(q,self.eq/ep) if ep>0 else 0
        if q<=0: return
        ef=q*ep*COMM
        self.openp(T(ts=ct,sym=s,d=d,ep=ep,sl=slp,t1=t1,t2=t2,t3=t3,q=q,ra=ra,fee=ef,
            ht=ht,st=st,imp=f"{IMP_N}+",pb=pz,cf=ct2,a15=av,
            r1h=r1h if not pd.isna(r1h) else 0,
            r15=dl["rsi14"].iloc[di] if not pd.isna(dl["rsi14"].iloc[di]) else 0,
            slev=vs[-1],rd=rd,rr=RR[0]),i)

def run_debug(data):
    """Debug version to see what's blocking trades"""
    bt=BT()
    for s in SYMBOLS:
        print(f"\n{s}...",flush=True)
        ht=prep_htf(data[s][TF_HTF].copy())
        lt=prep_ltf(data[s][TF_LTF].copy()); lt=swings(lt)
        st=structure(lt)
        
        # Count all filter blocks manually
        counts = {"htf":0,"struct":0,"impulse":0,"pullback":0,"confirm":0,
                  "rsi":0,"vol":0,"chase":0,"swing":0,"sl_dist":0}
        
        pullback_events = []
        pullback._debug = False  # Enable for first few pullback calls
        
        for i in range(len(lt)):
            if i<2: continue
            di=i-1
            ct=lt["timestamp"].iloc[i]
            hm=ht["timestamp"]<=ct
            if not hm.any(): continue
            hi=ht[hm].index[-1]
            
            # HTF
            h=htf_trend(ht.iloc[hi])
            if h=="N": counts["htf"]+=1; continue
            
            # Structure
            s_val=st.iloc[di]
            if s_val=="R" or (h=="B" and s_val!="BULLISH") or (h=="S" and s_val!="BEARISH"):
                counts["struct"]+=1; continue
            
            d="B" if h=="B" else "S"
            
            # Impulse
            if not impulse(lt,di,d): counts["impulse"]+=1; continue
            
            # Pullback
            ir=imp_range(lt,di,d)
            # Enable debug for first 3 pullback checks
            if counts["pullback"] < 3:
                pullback._debug = True
            else:
                pullback._debug = False
            pb,pz=pullback(lt,di,d,ir)
            if not pb: counts["pullback"]+=1; continue
            
            # Store pullback event
            pullback_events.append({"idx":i, "ts":ct, "dir":d, "zone":pz})
            
            # Confirmation
            cv,ct2=confirm(lt,i,d)
            if not cv:
                counts["confirm"]+=1
                # Debug: print first few failed confirmations
                if counts["confirm"] <= 5:
                    o,c,h2,l=lt["open"].iloc[i],lt["close"].iloc[i],lt["high"].iloc[i],lt["low"].iloc[i]
                    po,pc,ph=lt["open"].iloc[i-1],lt["close"].iloc[i-1],lt["high"].iloc[i-1]
                    body=abs(c-o)
                    rng=h2-l if h2>l else 0.0001
                    print(f"  CONF FAIL #{counts['confirm']}: {ct} {d} zone={pz}")
                    print(f"    CANDLE: o={o:.2f} c={c:.2f} h={h2:.2f} l={l:.2f} body={body:.2f}")
                    print(f"    PREV:   po={po:.2f} pc={pc:.2f} ph={ph:.2f}")
                    print(f"    BULL ENGULF: c>o={c>o} pc<po={pc<po} c>po={c>po} o<pc={o<pc} body>prev_body={body>abs(pc-po)}")
                    ls=min(o,c)-l
                    print(f"    PIN BAR: lower_shadow={ls:.2f} > 2*body={2*body:.2f} = {ls>2*body} close_pos={(c-l)/rng:.2f}")
                    print(f"    BREAK: c>ph={c>ph} c>o={c>o}")
                continue
            
            # If we get here, we would enter
            print(f"  SIGNAL at {ct}: {h} {s_val} {d} imp pb={pz} cf={ct2}")
        
        print(f"\n  Filter counts for {s}:")
        for k,v in counts.items():
            print(f"    {k}: {v}")
        
        print(f"  Total pullback events: {len(pullback_events)}")
        
        if s in bt.op: bt.closep(s,lt["close"].iloc[-1],"eod",len(lt)-1,lt["timestamp"].iloc[-1])
    return bt

def run(data):
    bt=BT()
    for s in SYMBOLS:
        print(f"\n{s}...",flush=True)
        ht=prep_htf(data[s][TF_HTF].copy())
        lt=prep_ltf(data[s][TF_LTF].copy()); lt=swings(lt)
        # Precompute structure once
        st=structure(lt)
        
        # Debug: check HTF trends
        ht_trends = [htf_trend(ht.iloc[i]) for i in range(len(ht))]
        print(f"  HTF trends: B={ht_trends.count('B')}, S={ht_trends.count('S')}, N={ht_trends.count('N')}")
        
        # Debug: check structures
        bull_struct = (st == "BULLISH").sum()
        bear_struct = (st == "BEARISH").sum()
        range_struct = (st == "RANGE").sum()
        print(f"  Structures: BULLISH={bull_struct}, BEARISH={bear_struct}, RANGE={range_struct}")
        
        # Debug: check ATR
        atr_vals = lt["atr14"].dropna()
        if len(atr_vals) > 0:
            print(f"  ATR: min={atr_vals.min():.4f}, max={atr_vals.max():.4f}, mean={atr_vals.mean():.4f}")
            print(f"  Price: last={lt['close'].iloc[-1]:.2f}")
            print(f"  ATR/Price: {(atr_vals/lt['close'].iloc[-1]).mean():.4f}")
        
        # Debug: check EMA200 warmup
        ema200_valid = ht["ema200"].notna().sum()
        print(f"  EMA200 valid candles: {ema200_valid}/{len(ht)}")
        
        bt.proc_precomp(s,ht,lt,st)
        if s in bt.op: bt.closep(s,lt["close"].iloc[-1],"eod",len(lt)-1,lt["timestamp"].iloc[-1])
        print(f"  done",flush=True)
    return bt

def report(bt):
    print("\n"+"="*60+"\nTPC BACKTEST RESULTS\n"+"="*60)
    n=len(bt.trades)
    if n==0: print("NO TRADES"); return
    
    w=[t for t in bt.trades if t.pnl>0]; l=[t for t in bt.trades if t.pnl<=0]
    wr=len(w)/n*100; gp=sum(t.pnl for t in w); gl=abs(sum(t.pnl for t in l))
    pf=gp/gl if gl>0 else float('inf'); net=sum(t.pnl for t in bt.trades)
    fees=sum(t.fee for t in bt.trades); slip=sum(t.sc for t in bt.trades)
    ar=sum(t.rm for t in bt.trades)/n
    mcl=cc=0
    for t in bt.trades:
        if t.pnl<=0: cc+=1; mcl=max(mcl,cc)
        else: cc=0
    fe=bt.eq; rp=(fe-INITIAL_CAPITAL)/INITIAL_CAPITAL*100
    
    print(f"\nPeriod: {MONTHS} months\nSymbols: {', '.join(SYMBOLS)}\nInitial: {INITIAL_CAPITAL:.2f} USDT")
    print(f"\nTrades: {n}\nWin rate: {wr:.1f}%\nPF: {pf:.2f}\nExpectancy: {net/n:.2f} USDT\nAvg R: {ar:.2f}")
    print(f"Max DD: {bt.mdd*100:.1f}%\nMax consec losses: {mcl}")
    print(f"\nFees: {fees:.2f}\nSlippage: {slip:.2f}")
    print(f"\nFinal: {fe:.2f}\nPnL: {net:.2f}\nReturn: {rp:.2f}%")
    
    print("\n"+"="*40+"\nBY SYMBOL\n"+"="*40)
    for s in SYMBOLS:
        st=[t for t in bt.trades if t.sym==s]
        if not st: print(f"{s}: 0 trades"); continue
        sp=sum(t.pnl for t in st); sw=len([t for t in st if t.pnl>0])/len(st)*100
        print(f"{s}: {len(st)} trades, WR {sw:.1f}%, PnL {sp:.2f}")
    
    print("\n"+"="*40+"\nBY DIRECTION\n"+"="*40)
    for d in ["B","S"]:
        dt=[t for t in bt.trades if t.d==d]
        if not dt: print(f"{'BUY' if d=='B' else 'SELL'}: 0"); continue
        dp=sum(t.pnl for t in dt); dw=len([t for t in dt if t.pnl>0])/len(dt)*100
        print(f"{'BUY' if d=='B' else 'SELL'}: {len(dt)} trades, WR {dw:.1f}%, PnL {dp:.2f}")
    
    print("\n"+"="*40+"\nFILTERS\n"+"="*40)
    for r,c in sorted(bt.bk.items(),key=lambda x:-x[1]): print(f"  {r}: {c}")
    
    # CSV
    with open(os.path.join(DIR,"tpc_trades.csv"),"w",newline="",encoding="utf-8") as f:
        w2=csv.writer(f)
        w2.writerow(["ts","sym","dir","entry","sl","t1","t2","t3","exit","reason","qty","risk","pnl","pnl%","R","fee","slip","dur","htf","str","imp","pb","cf","a15","r1h","r15","slev","rd","rr"])
        for t in bt.trades:
            w2.writerow([t.ts,t.sym,t.d,t.ep,t.sl,t.t1,t.t2,t.t3,t.xp,t.xr,t.q,t.ra,t.pnl,t.pp,t.rm,t.fee,t.sc,t.dur,t.ht,t.st,t.imp,t.pb,t.cf,t.a15,t.r1h,t.r15,t.slev,t.rd,t.rr])
    
    edf=pd.DataFrame(bt.ec)
    if len(edf)>0:
        edf.to_csv(os.path.join(DIR,"equity_curve.csv"),index=False)
        fig,ax=plt.subplots(figsize=(14,6))
        ax.plot(edf["ts"],edf["eq"],linewidth=1.5)
        ax.axhline(y=INITIAL_CAPITAL,color="gray",ls="--",alpha=.5)
        ax.set_title("TPC - Equity Curve"); ax.grid(True,alpha=.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        plt.xticks(rotation=45); plt.tight_layout()
        plt.savefig(os.path.join(DIR,"equity_curve.png"),dpi=150); plt.close()
        
        fig,ax=plt.subplots(figsize=(14,4))
        eq=edf["eq"].values; rm=np.maximum.accumulate(eq); dd=(rm-eq)/rm*100
        ax.fill_between(edf["ts"],0,-dd,alpha=.5,color="red")
        ax.set_title("TPC - Drawdown"); ax.grid(True,alpha=.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        plt.xticks(rotation=45); plt.tight_layout()
        plt.savefig(os.path.join(DIR,"drawdown.png"),dpi=150); plt.close()
    
    # VERDICT
    print("\n"+"="*60+"\nFINAL RESULT\n"+"="*60)
    print(f"\nPeriod: {MONTHS} months\nSymbols: {', '.join(SYMBOLS)}\nCapital: {INITIAL_CAPITAL:.2f}")
    print(f"\nTrades: {n}\nWR: {wr:.1f}%\nPF: {pf:.2f}\nExpectancy: {net/n:.2f}\nAvg R: {ar:.2f}")
    print(f"Max DD: {bt.mdd*100:.1f}%\nConsec losses: {mcl}")
    print(f"\nFees: {fees:.2f}\nSlippage: {slip:.2f}")
    print(f"\nEquity: {fe:.2f}\nPnL: {net:.2f}\nReturn: {rp:.2f}%")
    
    print("\n"+"="*60+"\nVERDICT\n"+"="*60)
    pf_p=pf>1.3; exp_p=net/n>0; dd_p=bt.mdd<0.15
    if pf_p and exp_p and dd_p:
        print(f"\nPASS\n  PF={pf:.2f}>1.3, Exp={net/n:.2f}>0, DD={bt.mdd*100:.1f}%<15%")
    else:
        print(f"\nFAIL")
        if not pf_p: print(f"  PF={pf:.2f}<=1.3")
        if not exp_p: print(f"  Expectancy={net/n:.2f}<=0")
        if not dd_p: print(f"  DD={bt.mdd*100:.1f}%>=15%")

if __name__=="__main__":
    print("="*60+"\nTPC Backtest\n"+"="*60)
    print(f"Symbols: {SYMBOLS}\nPeriod: {MONTHS}m\n")
    print("Fetching...",flush=True)
    data=fetch_all()
    print("\nRunning debug...",flush=True)
    bt=run_debug(data)
    print("\nResults...",flush=True)
    report(bt)
    print(f"\nSaved: {DIR}")
