"""Nightly European stock price collector for the '€1,000 Strategy League' artifact.

Universe: every member of each European country's main and mid-cap indices (see INDEXES), assembled each run
from pytickersymbols and the indices' Wikipedia member tables. Prices: daily, dividend- and split-adjusted
closes from Yahoo Finance via yfinance, in each listing's own currency, plus daily EUR exchange rates.
Outputs (published as release assets under tag "data"):
  eu_prices.parquet   date, ticker, adj_close, currency
  eu_universe.csv     ticker, name, index, country, currency
  eu_report.json      members found per index, tickers with/without data, last date
"""
import io, json, re, sys, time, datetime as dt, os
import pandas as pd, requests, yfinance as yf
from pytickersymbols import PyTickerSymbols

UA={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36','Accept':'text/csv,text/plain,*/*'}
# suffix -> (country, currency); London quotes are in pence (GBp)
SUFFIX={'.L':('UK','GBp'),'.DE':('DE','EUR'),'.PA':('FR','EUR'),'.AS':('NL','EUR'),'.BR':('BE','EUR'),'.MC':('ES','EUR'),
        '.MI':('IT','EUR'),'.LS':('PT','EUR'),'.VI':('AT','EUR'),'.IR':('IE','EUR'),'.HE':('FI','EUR'),'.SW':('CH','CHF'),
        '.ST':('SE','SEK'),'.CO':('DK','DKK'),'.OL':('NO','NOK'),'.WA':('PL','PLN')}
PTS_INDEXES={'AEX':'.AS','BEL 20':'.BR','CAC_40':'.PA','CAC Mid 60':'.PA','DAX':'.DE','MDAX':'.DE','SDAX':'.DE','TecDAX':'.DE',
             'EURO STOXX 50':None,'FTSE 100':'.L','IBEX 35':'.MC','OMX Helsinki 25':'.HE','OMX Stockholm 30':'.ST','Switzerland 20':'.SW'}
WIKI={  # index name -> (url, Yahoo suffix)
 'FTSE 100':('https://en.wikipedia.org/wiki/FTSE_100_Index','.L'),
 'FTSE 250':('https://en.wikipedia.org/wiki/FTSE_250_Index','.L'),
 'AEX':('https://en.wikipedia.org/wiki/AEX_index','.AS'),
 'AMX':('https://en.wikipedia.org/wiki/AMX_index','.AS'),
 'SBF 120':('https://en.wikipedia.org/wiki/SBF_120','.PA'),
 'CAC 40':('https://en.wikipedia.org/wiki/CAC_40','.PA'),
 'DAX':('https://en.wikipedia.org/wiki/DAX','.DE'),
 'MDAX':('https://en.wikipedia.org/wiki/MDAX','.DE'),
 'SDAX':('https://en.wikipedia.org/wiki/SDAX','.DE'),
 'FTSE MIB':('https://en.wikipedia.org/wiki/FTSE_MIB','.MI'),
 'FTSE Italia Mid Cap':('https://en.wikipedia.org/wiki/FTSE_Italia_Mid_Cap','.MI'),
 'IBEX 35':('https://en.wikipedia.org/wiki/IBEX_35','.MC'),
 'IBEX Medium Cap':('https://en.wikipedia.org/wiki/IBEX_Medium_Cap','.MC'),
 'SMI':('https://en.wikipedia.org/wiki/Swiss_Market_Index','.SW'),
 'SMI MID':('https://en.wikipedia.org/wiki/SMI_MID','.SW'),
 'OMX Stockholm 30':('https://en.wikipedia.org/wiki/OMX_Stockholm_30','.ST'),
 'OMX Copenhagen 25':('https://en.wikipedia.org/wiki/OMX_Copenhagen_25','.CO'),
 'OMX Helsinki 25':('https://en.wikipedia.org/wiki/OMX_Helsinki_25','.HE'),
 'OBX':('https://en.wikipedia.org/wiki/OBX_Index','.OL'),
 'BEL 20':('https://en.wikipedia.org/wiki/BEL_20','.BR'),
 'PSI':('https://en.wikipedia.org/wiki/PSI-20','.LS'),
 'ATX':('https://en.wikipedia.org/wiki/Austrian_Traded_Index','.VI'),
 'ISEQ 20':('https://en.wikipedia.org/wiki/ISEQ_20','.IR'),
 'WIG20':('https://en.wikipedia.org/wiki/WIG20','.WA'),
 'mWIG40':('https://en.wikipedia.org/wiki/MWIG40','.WA'),
}
TICK_COL=re.compile(r'ticker|symbol|epic|code|mnemonic|isin', re.I)
NAME_COL=re.compile(r'company|name|constituent', re.I)

def from_pts():
    """pytickersymbols' own 'symbol' field is the home listing in Yahoo form (e.g. ADS.DE, AI.PA, ADDT-B.ST)."""
    out=[]; s=PyTickerSymbols()
    for idx in PTS_INDEXES:
        for st in s.get_stocks_by_index(idx):
            t=str(st.get('symbol') or '').upper()
            m=re.match(r'^(.+?)(\.[A-Z]{1,2})(\.[A-Z]{1,2})?$',t)   # 'AIR.PA.DE' -> 'AIR.PA'
            if m: t=m.group(1)+m.group(2)
            if not any(t.endswith(k) for k in SUFFIX) and PTS_INDEXES.get(idx) and t and '.' not in t:
                t=re.sub(r'[\s.]+','-',t)+PTS_INDEXES[idx]
            if any(t.endswith(k) for k in SUFFIX): out.append({'ticker':t,'name':st.get('name',''),'index':idx})
    return out

def clean_ticker(t,suf):
    t=re.sub(r'\[.*?\]','',str(t)).strip().upper()
    t=re.sub(r'^[A-Z]{2,6}:\s*','',t)                     # 'LON: AZN' -> 'AZN'
    if not t or t in('NAN','—','-','N/A') or re.match(r'^[A-Z]{2}[0-9A-Z]{9}[0-9]$',t): return None  # skip blanks/ISINs
    for k in SUFFIX:
        if t.endswith(k): return t
    t=re.sub(r'\s+(LN|NA|FP|GY|IM|SM|SW|SE|SS|DC|NO|FH|BB|PL|AV|ID|PW)$','',t)   # Bloomberg exchange codes
    t=re.sub(r'\.(L|LN|AS|NA|PA|FP|DE|GY|MI|IM|MC|SM|SW|SE|ST|SS|CO|DC|OL|NO|HE|FH|BR|BB|LS|PL|VI|AV|IR|ID|WA|PW)$','',t)
    t=re.sub(r'[\s.]+','-',t).strip('-')                 # 'VOLV B' / 'BT.A' -> 'VOLV-B' / 'BT-A'
    if not re.match(r'^[A-Z0-9][A-Z0-9-]{0,11}$',t): return None
    return t+suf

DEBUG={}
def search_ticker(name,suf):
    """Find the home-exchange Yahoo ticker for a company name (used when an index table lists names only)."""
    if not name or name.lower()=='nan': return None
    try:
        q=yf.Search(name,max_results=10).quotes
    except Exception: return None
    for x in q:
        sym=str(x.get('symbol',''))
        if sym.upper().endswith(suf) and x.get('quoteType') in ('EQUITY',None): return sym.upper()
    return None

# iShares STOXX Europe 600 UCITS ETF holdings (Europe's 600 largest companies across 17 countries)
ISHARES=[
 ('STOXX Europe 600','https://www.ishares.com/de/privatanleger/de/produkte/251931/ishares-stoxx-europe-600-ucits-etf-de-fund/1478358465952.ajax?fileType=csv&fileName=EXSA_holdings&dataType=fund'),
 ('S&P Europe 350','https://www.ishares.com/us/products/239740/ishares-europe-etf/1467271812596.ajax?fileType=csv&fileName=IEV_holdings&dataType=fund'),
]
EXCH={'xetra':'.DE','frankfurt':'.DE','euronext amsterdam':'.AS','euronext paris':'.PA','nyse euronext - euronext paris':'.PA','euronext brussels':'.BR',
      'euronext lisbon':'.LS','euronext dublin':'.IR','irish':'.IR','london':'.L','borsa italiana':'.MI','milan':'.MI','bolsa de madrid':'.MC','madrid':'.MC',
      'six swiss':'.SW','swiss':'.SW','stockholm':'.ST','helsinki':'.HE','copenhagen':'.CO','oslo':'.OL','wiener':'.VI','vienna':'.VI','warsaw':'.WA'}
def from_ishares():
    rep={}; allrows=[]
    for name,url in ISHARES:
        try:
            resp=requests.get(url,headers=UA,timeout=60); txt=resp.content.decode('utf-8-sig',errors='replace')
            DEBUG[f'ishares {name} {url[:60]}']=[resp.status_code,resp.headers.get('content-type'),txt[:900]]
            lines=txt.splitlines(); start=next(i for i,l in enumerate(lines) if re.search(r'(Emittententicker|Ticker)',l))
            sep=';' if lines[start].count(';')>lines[start].count(',') else ','
            df=pd.read_csv(io.StringIO('\n'.join(lines[start:])),dtype=str,sep=sep,on_bad_lines='skip')
            tcol=df.columns[0]; ncol=next((c for c in df.columns if re.search(r'^name$',c,re.I)),df.columns[1])
            xcol=next((c for c in df.columns if re.search(r'b(ö|o)rse|exchange',c,re.I)),None)
            acol=next((c for c in df.columns if re.search(r'anlageklasse|asset class',c,re.I)),None)
            rows=[]
            for _,r in df.iterrows():
                if acol and not re.search(r'aktie|equity',str(r[acol]),re.I): continue
                ex=str(r[xcol]).lower() if xcol else ''
                suf=next((v for k,v in EXCH.items() if k in ex),None)
                if not suf: continue
                t=clean_ticker(r[tcol],suf)
                if t: rows.append({'ticker':t,'name':str(r[ncol]).title(),'index':name})
            rep[url.split('/')[2]+':'+name]=len(rows)
            if len(rows)>300: allrows.extend(rows)
        except Exception as e:
            rep[url.split('/')[2]+':'+name]=f'failed: {e.__class__.__name__}: {str(e)[:80]}'
    return allrows,rep

NORDIC={'STO':'.ST','CPH':'.CO','HEL':'.HE'}
def from_nasdaq_nordic():
    """Nasdaq Nordic main-market shares in the Large Cap and Mid Cap segments (Stockholm, Copenhagen, Helsinki)."""
    out=[]; rep={}
    for mk,suf in NORDIC.items():
        for seg in ('LARGE_CAP','MID_CAP'):
            url=f'https://api.nasdaq.com/api/nordic/screener/shares?category=MAIN_MARKET&tableonly=false&market={mk}&segment={seg}'
            try:
                js=requests.get(url,headers={**UA,'Accept':'application/json'},timeout=60).json()
                rows=js.get('data',{}).get('instrumentListing',{}).get('rows') or []
                n=0
                for x in rows:
                    if x.get('assetClass') not in (None,'','SHARES'): continue
                    t=clean_ticker(x.get('symbol') or '',suf)
                    if t: out.append({'ticker':t,'name':x.get('fullName',''),'index':f'Nasdaq {mk} {seg.replace("_"," ").title()}'}); n+=1
                rep[f'Nasdaq {mk} {seg}']=n
            except Exception as e: rep[f'Nasdaq {mk} {seg}']=f'failed: {e.__class__.__name__}: {str(e)[:80]}'
            time.sleep(1)
    return out,rep

def from_wiki():
    out=[]; report={}
    for idx,(url,suf) in WIKI.items():
        try:
            html=requests.get(url,headers=UA,timeout=30).text
            tables=pd.read_html(io.StringIO(html))
            best=[]
            for tb in tables:
                cols=[str(c[-1] if isinstance(c,tuple) else c) for c in tb.columns]
                tc=[i for i,c in enumerate(cols) if TICK_COL.search(c) and 'isin' not in c.lower()]
                if len(tb)<8: continue
                if not tc:
                    nc=[i for i,c in enumerate(cols) if NAME_COL.search(c)]
                    if not nc or best: continue
                    rows=[]
                    for _,r in tb.iterrows():
                        nm=re.sub(r'\[.*?\]','',str(r.iloc[nc[0]])).strip()
                        t=search_ticker(nm,suf)
                        if t: rows.append({'ticker':t,'name':nm,'index':idx})
                    if len(rows)>len(best): best=rows
                    continue
                nc=[i for i,c in enumerate(cols) if NAME_COL.search(c)]
                sc=[i for i,c in enumerate(cols) if re.search(r'sector|industry',c,re.I)]
                rows=[]
                for _,r in tb.iterrows():
                    if sc and re.search(r'closed.end|investment trust|investment compan|equity investment instrument',str(r.iloc[sc[0]]),re.I): continue  # listed funds
                    t=clean_ticker(r.iloc[tc[0]],suf)
                    if t: rows.append({'ticker':t,'name':str(r.iloc[nc[0]]) if nc else '','index':idx})
                if len(rows)>len(best): best=rows
            out+=best; report[idx]=len(best)
            if not best: DEBUG[idx]=[[str(c) for c in tb.columns][:8] for tb in tables[:6]]
        except Exception as e:
            report[idx]=f'failed: {e.__class__.__name__}: {str(e)[:80]}'
        time.sleep(1)
    return out,report

def universe():
    rows=from_pts(); wiki,wrep=from_wiki(); rows+=wiki
    ish,irep=from_ishares(); rows+=ish; wrep.update(irep)
    nn,nrep=from_nasdaq_nordic(); rows+=nn; wrep.update(nrep)
    df=pd.DataFrame(rows)
    df['suffix']=df.ticker.str.extract(r'(\.[A-Z]{1,2})$')[0]
    df=df[df.suffix.isin(SUFFIX)]
    df['country']=df.suffix.map(lambda s:SUFFIX[s][0]); df['currency']=df.suffix.map(lambda s:SUFFIX[s][1])
    agg=df.groupby('ticker').agg(name=('name',lambda x:max(x,key=len)),index=('index',lambda x:', '.join(sorted(set(x)))),country=('country','first'),currency=('currency','first')).reset_index()
    return agg,wrep

def download(tickers,start):
    frames=[]; ok=set()
    for i in range(0,len(tickers),80):
        chunk=tickers[i:i+80]
        for attempt in range(3):
            try:
                d=yf.download(chunk,start=start,interval='1d',auto_adjust=True,progress=False,group_by='ticker',threads=True)
                break
            except Exception: time.sleep(20*(attempt+1)); d=None
        if d is None or d.empty: continue
        for t in chunk:
            try: s=d[t]['Close'] if len(chunk)>1 else d['Close']
            except KeyError: continue
            s=s.dropna()
            if len(s): frames.append(pd.DataFrame({'date':s.index.date,'ticker':t,'adj_close':s.values.astype(float)})); ok.add(t)
        time.sleep(3)
    return (pd.concat(frames) if frames else pd.DataFrame(columns=['date','ticker','adj_close'])),ok

def main():
    full='--full' in sys.argv
    uni,wrep=universe(); uni.to_csv('eu_universe.csv',index=False)
    old=None
    if not full and os.path.exists('eu_prices.parquet'):
        old=pd.read_parquet('eu_prices.parquet'); old=old[old.currency!='FX'][['date','ticker','adj_close']]
        old=old[old.ticker.isin(set(uni.ticker))]
    if old is not None and len(old):
        known=set(old.ticker); new=[t for t in uni.ticker if t not in known]
        upd,ok1=download([t for t in uni.ticker if t in known],(dt.date.today()-dt.timedelta(days=14)).isoformat())
        hist,ok2=download(new,'2016-01-01') if new else (pd.DataFrame(columns=upd.columns),set())
        px=pd.concat([old,upd,hist]); ok=ok1|ok2|known
    else:
        px,ok=download(list(uni.ticker),'2016-01-01')
    px['date']=pd.to_datetime(px.date).dt.date
    px=px.drop_duplicates(['date','ticker'],keep='last').sort_values(['ticker','date'])
    px=px.merge(uni[['ticker','currency']],on='ticker',how='left')
    # EUR exchange rates (units of currency per 1 EUR); GBp handled downstream as GBP/100
    fx,_=download(['EURUSD=X','EURGBP=X','EURCHF=X','EURSEK=X','EURDKK=X','EURNOK=X','EURPLN=X'],'2016-01-01')
    fx['currency']='FX'
    pd.concat([px,fx]).to_parquet('eu_prices.parquet',index=False)
    rep={'run':dt.datetime.utcnow().isoformat(timespec='minutes')+'Z','universe':int(len(uni)),'with_data':int(px.ticker.nunique()),
         'missing':sorted(set(uni.ticker)-set(px.ticker))[:300],'last_date':str(px.date.max()),'wikipedia_members_found':wrep,
         'by_country':uni.country.value_counts().to_dict(),'debug_tables':DEBUG}
    json.dump(rep,open('eu_report.json','w'),indent=1); print(json.dumps({k:v for k,v in rep.items() if k!='missing'},indent=1))

if __name__=='__main__': main()
