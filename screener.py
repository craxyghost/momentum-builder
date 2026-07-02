"""
Momentum Screener Engine
========================
Scans NSE & NYSE stocks across Large / Mid / Small Cap categories,
keeps only those scoring 81-100.
Micro Cap excluded — insufficient liquidity for reliable momentum signals.
Runs monthly automatically via APScheduler.
Results persisted in data/ as JSON.
"""

import json
import os
import time
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf
import pandas as pd

from indicators import (
    calculate_all_indicators,
    indicator_price_momentum, indicator_rsi, indicator_macd,
    indicator_52week_high, indicator_ma_momentum,
    indicator_intermediate_momentum, indicator_momentum_consistency,
    indicator_multi_horizon_confluence,
    INDICATOR_WEIGHTS, INDICATOR_META,
)

# ── Paths ──────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)


# ══════════════════════════════════════════════════════════════════
#  NSE UNIVERSE  — Large / Mid / Small Cap
#  Source: Nifty 100, Nifty Midcap 150, Nifty Smallcap 250
# ══════════════════════════════════════════════════════════════════

_NSE_LARGE = [
    # Nifty 50 + Nifty Next 50 (Large Cap — Nifty 100)
    'ADANIENT','ADANIPORTS','ADANIGREEN','ADANITRANS','ADANIPOWER',
    'APOLLOHOSP','ASIANPAINT','ATGL','AUBANK','AXISBANK',
    'BAJAJ-AUTO','BAJAJFINSV','BAJFINANCE','BAJAJHLDNG','BANKBARODA',
    'BERGEPAINT','BHARTIARTL','BEL','BPCL','BRITANNIA',
    'CANBK','CHOLAFIN','CIPLA','COALINDIA','COLPAL',
    'CONCOR','CUMMINSIND','DABUR','DLF','DIVISLAB',
    'DMART','DRREDDY','EICHERMOT','GAIL','GODREJCP',
    'GODREJPROP','GRASIM','HAVELLS','HCLTECH','HDFCAMC',
    'HDFCBANK','HDFCLIFE','HEROMOTOCO','HINDALCO','HINDUNILVR',
    'ICICIBANK','ICICIGI','ICICIPRULI','IGL','INDHOTEL',
    'INDUSINDBK','INFY','INDUSTOWER','IRCTC','ITC',
    'JINDALSTEL','JUBLFOOD','JSWSTEEL','KOTAKBANK','LT',
    'LICHSGFIN','LTIM','LUPIN','M&M','MARICO',
    'MARUTI','MAXHEALTH','MCDOWELL-N','MOTHERSON','MPHASIS',
    'MUTHOOTFIN','NAUKRI','NESTLEIND','NHPC','NMDC',
    'NTPC','OBEROIRLTY','ONGC','PAGEIND','PERSISTENT',
    'PETRONET','PFC','PIDILITIND','PIIND','POLYCAB',
    'POWERGRID','PRESTIGE','RECLTD','RELIANCE','SAIL',
    'SBICARD','SBILIFE','SBIN','SHREECEM','SIEMENS',
    'SUNPHARMA','TATACONSUM','TATAELXSI','TATAPOWER','TATACHEM',
    'TATAMOTORS','TATASTEEL','TCS','TECHM','TITAN',
    'TORNTPHARM','TRENT','TVSMOTOR','ULTRACEMCO','UNIONBANK',
    'VBL','VEDL','VOLTAS','WIPRO','ZEEL','ZOMATO',
    # Other prominent large caps
    'ABB','AMBUJACEM','APOLLOTYRE','AUROPHARMA','BIOCON',
    'BOSCHLTD','DEEPAKNTR','ESCORTS','EXIDEIND','FORTIS',
    'GMRINFRA','INDIAMART','JUBLINGREA','KAJARIACER','MFSL',
    'OFSS','PVRINOX','RAYMOND','SUNTV','TRIDENT',
]

_NSE_MID = [
    # Nifty Midcap 150
    'AARTIIND','ABCAPITAL','APLAPOLLO','ASTRAL','BALAMINES',
    'BALKRISIND','BATAINDIA','BSOFT','CAMS','CAMPUS',
    'CAPLIPOINT','CDSL','CENTURYPLY','CGPOWER','COFORGE',
    'CYIENT','DATAMATICS','DCMSHRIRAM','DEEPAKFERT','DELTACORP',
    'DEVYANI','DHANUKA','DIXON','ELGIEQUIP','EMAMILTD',
    'EPLLTD','EQUITASBNK','FEDERALBNK','FINEORG','FIRSTSOURCE',
    'GNFC','GODREJAGRO','GRANULES','GSPL','GUJGASLTD',
    'HAPPSTMNDS','HDFCAMC','IIFLWAM','IDFCFIRSTB','INTELLECT',
    'IPCALAB','IRCON','JBCHEPHARM','JKCEMENT','KALPATPOWR',
    'KARURVYSYA','KEI','KPIL','KPITTECH','LATENTVIEW',
    'LINDEINDIA','LTTS','LUXIND','MAHINDCIE','MANAPPURAM',
    'METROPOLIS','MIDHANI','MRF','NATCOPHARM','NATIONALUM',
    'NAVINFLUOR','NBCC','NESCO','NEULANDLAB','NUVAMA',
    'NYKAA','OLECTRA','PCBL','PRAJIND','QUESS',
    'RADICO','RAIN','RAMCOCEM','RITES','ROUTE',
    'RVNL','SAFARI','SANSERA','SCHAEFFLER','SEQUENT',
    'SJVN','SOBHA','SONATSOFTW','STARCEMENT','SUPREMEIND',
    'SYMPHONY','TANLA','TATATECH','TIINDIA','TIMKEN',
    'TITAGARH','TTKPRESTIG','UJJIVANSFB','UCOBANK','UNOMINDA',
    'VAIBHAVGBL','VSTIND','WABAG','WELSPUNIND','WESTLIFE',
    'ZYDUSLIFE','AFFLE','AJANTPHARM','ALKEM','CANFINHOME',
    'CESC','CROMPTON','EDELWEISS','FORCEMOT','GESHIP',
    'GRSE','HUDCO','JKLAKSHMI','JKPAPER','JMFINANCIL',
    'JSL','JUBLPHARMA','JUSTDIAL','KFINTECH','KIMS',
    'KTKBANK','LAURUSLABS','MOTILALOFS','NATCOPHARM','NETWORK18',
    'ORCHIDPHRM','PHOENIXLTD','POLYPLEX','POONAWALLA','RBLBANK',
    'RENUKA','RHIM','RPOWER','SAREGAMA','SHANKARA',
    'SHILPAMED','SKFINDIA','SURYAROSNI','SWSOLAR','TATAINVEST',
    'TVSSRICHAK','VOLTAMP','ZEEMEDIA','GLAND','PFIZER',
]

_NSE_SMALL = [
    # Nifty Smallcap 250
    'ADFFOODS','AAVAS','ALKYLAMINE','ANUPAM','ASAHIINDIA',
    'ASTRAZEN','AVANTIFEED','BECTORFOOD','BIKAJI','BLUESTARCO',
    'BOROLTD','CANTABIL','CARBORUNIV','CASTROLIND','CCL',
    'CENTURYTEX','CHEMCON','CHEMPLASTS','CLEAN','COCHINSHIP',
    'CONCORDBIO','CRISIL','DBREALTY','DHANI','DLINKINDIA',
    'EQUITAS','ESABINDIA','ETHOS','GAEL','GALAXYSURF',
    'GARFIBRES','GILLETTE','GLAXO','GMDC','GOLDIAM',
    'GUJALKALI','HFCL','HIMATSEIDE','IDFC','IIFL',
    'INOXGREEN','INOXWIND','IRB','JPPOWER','KRBL',
    'KSCL','LAXMIMACH','MANINFRA','MARKSANS','MAZDA',
    'MMTC','MOLDTKPAC','MSTCLTD','NIITLTD','NILKAMAL',
    'PAISALO','PRINCEPIPES','PRUDENT','QUICKHEAL','RAJRATAN',
    'SHARDA','SJVN','TATVA','TIPSINDLTD','TTKHLTCARE',
    'VHL','VIPCLOTH','VSTTISL','XPRO','ZIMLAB',
    'AMIORG','ARVINDFASN','ASTERDM','ASTER','ATISHAY',
    'AVADHSUGAR','BAJAJCON','BDAL','BIRLASOFT','BPCL',
    'CAPLIPOINT','CIGNITI','CLNINDIA','CODIAGNOSTIX','CSBBANK',
    'CYNOCHIPS','DBREALTY','DBSTOCKBRO','DCAL','DECCANCE',
    'EIDPARRY','EMKAY','ENGINERSIN','FACT','FIVESTAR',
    'FLFL','GHCLTEXTIL','GLOBALHLTH','GNFC','GODFRYPHLP',
    'GPIL','GREENPANEL','GRINDWELL','GSFC','GUJRAFFIA',
    'HAPPIEST','HARSHA','HECL','HEIDELBERG','HGS',
    'HINDCOPPER','HINDPETRO','HONAUT','IGSB','IMSHEALTH',
    'INDIAGLYCOL','INDOCO','INFIBEAM','INTELLECT','IPCALAB',
    'ISGEC','ITDCEM','JKIL','JKTYRE','JLHL',
    'JNKINDIA','JPASSOCIAT','KALYANKJIL','KANANIIND','KECINTELE',
    'KIOCL','KITEX','KMCCONST','KOPRAN','KPRMILL',
    'KRBL','KRIBHCO','KRUSTEAZ','KSOLVES','KWALITY',
    'LANDMARK','LANCER','LGBBROSLTD','LLOYDMETAL','LOKESHM',
    'LUMAXIND','LUMAXTECH','MAHLIFE','MAHSCOOTER','MASFIN',
    'MAXESTATES','MBAPL','MPSLTD','MRPL','MUKTA',
    'NAVNETEDUL','NBCC','NBLGOLD','NDTV','NELCAST',
    'NILAINFRA','NKIND','NOCIL','NUVOCO','OCCL',
    'OLECTRA','ONMOBILE','OPTIEMUS','ORIANA','ORIENTCEM',
    'PARADEEP','PCJEWELLER','PDMJEPAPER','PENIND','PIONDIST',
    'POCL','PNBGILTS','POCL','POLSON','PRAKASHIND',
    'PURAVANKARA','RAMASTEEL','RAMCOIND','RANEENGRO','RANEHOLDIN',
    'RATNAMANI','RDBRL','REDINGTON','RFCL','ROHLTD',
    'ROLEXRINGS','RPGLIFE','RSWM','RUBYMILLS','SALSTEEL',
    'SANGHIIND','SARLA','SBFC','SBIN','SEPC',
    'SHYAMMETL','SILINV','SINTERCOM','SKMEGG','SOLARINDS',
    'SONACOMS','SPENCERS','SREINFRA','SSWL','STCINDIA',
    'STLTECH','SUBROS','SUNFLAG','SUNPHARMA','SUPRAJIT',
    'SURANAT','SURAJEST','SWSOLAR','TASTYBITE','TITAGARH',
    'TPLPLASTEH','TRIL','UMANGDAIRY','USHAMART','V2RETAIL',
    'VEDL','VIRINCHI','VISHNU','VSTIND','WOCKPHARMA',
    'YESBANK','ZENTEC',
]

_NSE_MICRO = []  # Micro Cap removed — insufficient liquidity for momentum signals

# ── NSE Master Lists ───────────────────────────────────────────────
_NSE_CATEGORY_MAP = {}
for s in _NSE_LARGE: _NSE_CATEGORY_MAP[s] = 'Large Cap'
for s in _NSE_MID:   _NSE_CATEGORY_MAP[s] = 'Mid Cap'
for s in _NSE_SMALL: _NSE_CATEGORY_MAP[s] = 'Small Cap'
for s in _NSE_MICRO: _NSE_CATEGORY_MAP[s] = 'Micro Cap'

# Deduplicate — keep first occurrence (largest cap)
_seen = set()
NSE_STOCKS = []
for lst in (_NSE_LARGE, _NSE_MID, _NSE_SMALL, _NSE_MICRO):
    for s in lst:
        if s not in _seen:
            _seen.add(s)
            NSE_STOCKS.append(s)


# ══════════════════════════════════════════════════════════════════
#  NYSE/NASDAQ UNIVERSE — Large / Mid / Small Cap
#  Source: S&P 500, S&P 400, S&P 600
# ══════════════════════════════════════════════════════════════════

_NYSE_LARGE = [
    # S&P 500 — Mega & Large Cap
    'AAPL','MSFT','NVDA','AMZN','GOOGL','META','BRK-B','LLY','AVGO','JPM',
    'TSLA','UNH','XOM','V','MA','JNJ','PG','HD','MRK','ABBVIE',
    'COST','CVX','KO','BAC','ORCL','WMT','MCD','CRM','PEP','NFLX',
    'TMO','ACN','ABT','GE','AMD','TXN','QCOM','HON','AMGN','LIN',
    'DHR','SPGI','ISRG','BKNG','SYK','GS','MS','BLK','AXP','GILD',
    'MDT','VRTX','REGN','BSX','CB','MMC','PLD','AMT','NEE','DUK',
    'RTX','LMT','NOC','GD','BA','CAT','DE','ETN','PH','UNP',
    'UPS','FDX','LOW','TGT','SBUX','NKE','TJX','ELV','CI','HUM',
    'CVS','MCK','WFC','USB','PNC','ANET','NOW','DDOG','NET','CRWD',
    'PANW','ZS','SNOW','PLTR','UBER','ABNB','COIN','PYPL','MDB','COF',
    'IBM','CSCO','ADBE','INTU','ADSK','FTNT','SNPS','CDNS','ANSS','ROP',
    'IDXX','EW','STE','MTD','WAT','A','KEYS','TER','KLAC','LRCX',
    'AMAT','MRVL','MCHP','MPWR','ENTG','SWKS','NXPI','ADI','FSLR','ENPH',
    'AIG','ALL','AON','PRU','MET','AFL','TRV','HIG','CINF','ERIE',
    'RGA','RE','BRO','WRB','ACGL','RNR','PGR','CB','ARCH','MMC',
    'SO','AEP','SRE','D','PCG','EXC','XEL','ES','WEC','ED',
    'SPG','O','PSA','DLR','WELL','CCI','EQIX','AVB','EQR','MAA',
    'HST','KIM','NNN','VICI','WP','BXP','IRM','PEAK','CUZ','CPT',
    'F','GM','STLA','HOG','APTV','VC','BWA','AXL','SMP','THRM',
    'JPM','BAC','WFC','GS','MS','C','BK','STT','SCHW','NTRS',
    'CME','ICE','NDAQ','CBOE','MKTX','LPLA','RJF','AMTD','SF','PIPR',
    'AMZN','GOOGL','META','NFLX','DIS','CMCSA','WBD','PARA','FOX','FOXA',
    'JNJ','PFE','MRK','ABBVIE','BMY','AMGN','GILD','BIIB','REGN','VRTX',
    'WMT','TGT','COST','HD','LOW','TJX','ROST','DG','DLTR','KR',
    'MCD','SBUX','CMG','YUM','DPZ','QSR','WEN','JACK','SHAK','WING',
]

_NYSE_MID = [
    # S&P 400 MidCap
    'GNRC','MTZ','LSTR','WSO','HXL','HBI','HRB','HBAN','HIW','HII',
    'HOLX','HPP','HQY','HURN','HWM','IART','IDEX','ITT','JBL','JBT',
    'JELD','JHG','JKHY','JLL','KBH','KBR','KBAL','KFY','KIM','KMT',
    'KNSL','KNX','KTOS','LKQ','LANC','LHX','LNC','LEVI','LGND','LH',
    'LII','LITE','LNT','LPX','LSI','LUMN','M','MASI','MAT','MANH',
    'MDC','MHK','MIDD','MKC','MKSI','MLCO','MLM','MMI','MMS','MOH',
    'MPW','MRCY','MSA','MSCI','MTG','MTN','MUR','NBL','NCI','NLY',
    'NNN','NOV','NPO','NRG','NUS','NVCR','NWSA','NXRT','OLN','OMC',
    'ON','ONDK','OPK','OSK','OXY','PAG','PAYC','PDCE','PEGA','PEN',
    'PII','PKG','PKI','PNW','POST','PRI','PRO','PRGO','PSEG','PTC',
    'PTEN','PVAC','PXD','QLYS','QRVO','R','RBC','RCL','RE','REXR',
    'RIG','RLI','RMD','ROIC','RPM','RRC','RS','RGEN','RHI','RMBS',
    'RPD','RVMD','RYAN','RYN','SABR','SCI','SEE','SEIC','SF','SFM',
    'SITE','SKX','SLM','SNA','SMAR','SMCI','SNX','SON','SPB','SPSC',
    'SRCL','SRI','SSD','SSNC','STC','STRA','STRL','SUM','SWK','SWM',
    'SXC','SXT','SYF','SYY','TALO','TBBK','TCBI','TCMD','TCO','TDOC',
    'TENB','TFX','TGNA','THEN','THG','THO','TILE','TKR','TNC','TNET',
    'TNL','TOL','TRMK','TROW','TROX','TRST','TSE','TSCO','TTEK','TUP',
    'TVL','TXRH','UHAL','UIS','UNM','UNVR','URBN','USPH','UFCS','UFG',
    'UGI','UMBF','UMPQ','UNFI','UNF','USB','VBTX','VCRA','VCYT','VLY',
    'VMC','VNO','VOYA','VRTS','VSAT','VST','VTOL','WDFC','WEN','WFCF',
    'WGO','WINA','WIRE','WK','WLTW','WMS','WOR','WORTH','WPC','WPPGY',
    'WRK','WSBC','WSC','WSM','WST','WTBA','WTFC','WTRE','WTW','WULF',
    'WVE','WYND','XHR','XIN','XLF','XNCR','XPO','XRAY','XYL','YELP',
    'YRCW','ZETA','ZION','ZM','ZS','ZUO','ZVRA','ZYME','ACC','AIZ',
    'AKR','AM','AMRC','AMS','AMWD','ANF','AOS','APG','APOG','ARG',
    'ARI','ARIS','ARR','ASB','ASH','ASIX','ASO','ATI','ATNI','AWI',
    'AWR','AXS','AXSM','BCO','BECN','BIO','BLKB','BNL','BOH','BOOT',
    'BRC','BRKL','BRP','BWXT','BXS','CABO','CALX','CARG','CASY','CATO',
    'CBT','CCOI','CE','CENX','CENTA','CEVA','CFLT','CFR','CHE','CHDN',
    'CHS','CHX','CIB','CIEN','CIT','CLBK','CLH','CLOV','CMC','CMPR',
    'CNK','CNO','COHU','CPK','CPRT','CRI','CRUS','CRTO','CTT','CUBE',
    'CUBI','CURT','CWT','CXW','CZR','DAKT','DBX','DCI','DDS','DECK',
    'DEN','DFS','DKS','DLX','DNB','DOCS','DORM','DRVN','DSP','DXCM',
    'DYN','EAT','EBC','EFC','EFSC','EGP','EHTH','ELF','ENVB','EOG',
    'EPRT','EPAM','ESRT','ETRN','EVR','EXP','EXPI','EXPO','FBHS','FCPT',
    'FHB','FHI','FIVN','FLO','FMCN','FNB','FNF','FOXF','FRPT','FSB',
    'FTDR','FTI','GDDY','GEOS','GEO','GFF','GGG','GLDD','GLPI','GMED',
    'GOLF','GPK','GPN','GPRE','GTX','HAFC','HALO','HASI','HAYW','HE',
    'HECM','HFWA','HGTY','HLNE','HOME','HPE','HRC','HRL','HSII','HTLF',
]

_NYSE_SMALL = [
    # S&P 600 SmallCap — select
    'ABCB','ACIW','ACLS','ACNB','AEHR','AEIS','AENT','AFCG','AGIO','AGYS',
    'AHCO','AHH','AIOT','AIRG','AIRJ','AKAM','AKBA','AKRO','ALGT','ALGN',
    'ALGT','ALRM','ALSA','ALTG','ALTM','ALTR','ALXO','AMBC','AMCX','AMEH',
    'AMG','AMGN','AMNB','AMPH','AMPL','AMRB','AMRS','AMSC','AMWD','ANGI',
    'ANGO','ANH','ANIK','ANLG','ANSS','ANTM','APAM','APC','APEI','APOG',
    'APPN','APRE','AQST','AQN','ARCB','ARCL','ARCP','ARCT','ARDC','ARES',
    'ARLO','ARMT','ARNC','ARON','ARQT','ARRY','ARS','ARSD','ARSG','ARTNA',
    'ARTW','ARWR','ASIX','ASPN','ASPS','ASRT','ASTL','ASUR','ATEX','ATH',
    'ATHL','ATIF','ATLC','ATLO','ATRA','ATRC','ATRI','ATRO','ATSG','ATVI',
    'AUBN','AUPH','AUTO','AVAV','AVDL','AVEO','AVGO','AVID','AVP','AVPT',
    'AVRO','AXNX','AXON','AXSM','AYI','AZEK','AZPN','AZZ','B','BANC',
    'BAND','BANF','BANR','BBAR','BBCP','BBD','BBDC','BBW','BCAL','BCEI',
    'BCML','BCOV','BCPC','BCX','BFLY','BGFV','BGSF','BHVN','BIOL','BJRI',
    'BKE','BKKT','BKR','BKU','BLD','BLFS','BLMN','BLNK','BLX','BLUE',
    'BMRC','BNED','BNFT','BOCH','BPMC','BPOP','BRFS','BRG','BRKL','BRT',
    'BSET','BSMX','BSVN','BTBT','BUSE','BWFG','BXMT','BXSL','BYD','BYNO',
    'CACC','CADE','CALM','CAMP','CAMT','CAPN','CARE','CATO','CATX','CBNK',
    'CBU','CCAP','CCLP','CCNC','CCRN','CDXC','CDXS','CECO','CEI','CELC',
    'CELU','CENTA','CEPU','CEVA','CFF','CFFI','CFG','CFNB','CGEM','CGO',
    'CHCO','CHD','CHEF','CHGG','CHRS','CHUY','CIX','CKPT','CL','CLBK',
    'CLC','CLFD','CLPS','CLPT','CLRB','CLSD','CLVT','CMBM','CMCT','CMCSA',
    'CMGE','CMLS','CMRX','CMSD','CNOB','CNSL','CNTA','CNTQ','CODA','CODF',
    'CODI','COIN','COLB','COLL','COMM','COMP','COOP','CORR','CORS','COUR',
    'CPHA','CPIX','CPRT','CPSS','CR','CRAI','CRCT','CRDO','CRGE','CRMT',
    'CRNX','CRSP','CRTO','CRWS','CSBR','CSCW','CSGP','CSII','CSOD','CSTM',
    'CSTR','CTBI','CTLP','CTLT','CTOS','CTRE','CTSH','CTRL','CVAC','CVCO',
    'CVGW','CVI','CVLT','CVLY','CVRX','CWCO','CWH','CWST','CWT','CXM',
]

_NYSE_MICRO = []  # Micro Cap removed — insufficient liquidity for momentum signals

# ── NYSE Master Lists ──────────────────────────────────────────────
_NYSE_CATEGORY_MAP = {}
for s in _NYSE_LARGE: _NYSE_CATEGORY_MAP[s] = 'Large Cap'
for s in _NYSE_MID:   _NYSE_CATEGORY_MAP[s] = 'Mid Cap'
for s in _NYSE_SMALL: _NYSE_CATEGORY_MAP[s] = 'Small Cap'
for s in _NYSE_MICRO: _NYSE_CATEGORY_MAP[s] = 'Micro Cap'

_seen2 = set()
NYSE_STOCKS = []
for lst in (_NYSE_LARGE, _NYSE_MID, _NYSE_SMALL, _NYSE_MICRO):
    for s in lst:
        if s not in _seen2:
            _seen2.add(s)
            NYSE_STOCKS.append(s)


# ── In-memory progress tracking ────────────────────────────────────
_progress = {
    'NSE':  {'status': 'idle', 'started': None, 'done': 0, 'total': 0,
             'found': 0, 'current': '', 'errors': 0},
    'NYSE': {'status': 'idle', 'started': None, 'done': 0, 'total': 0,
             'found': 0, 'current': '', 'errors': 0},
}
_locks = {'NSE': threading.Lock(), 'NYSE': threading.Lock()}


# ── Helpers ────────────────────────────────────────────────────────

def _screener_path(exchange: str) -> str:
    return os.path.join(DATA_DIR, f'{exchange.lower()}_screener.json')


def load_screener(exchange: str) -> dict | None:
    path = _screener_path(exchange)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def get_progress(exchange: str) -> dict:
    return dict(_progress[exchange])


# ══════════════════════════════════════════════════════════════════
#  EXCLUSION POLICY
#  Excludes: Financial Services, Banking, Insurance, NBFC
#            Tobacco companies
#            Alcohol / Wine / Spirits / Brewery companies
# ══════════════════════════════════════════════════════════════════

# Industries returned by yfinance that are always excluded
_EXCLUDED_INDUSTRIES = {
    # Financial
    'Banks - Regional', 'Banks - Diversified', 'Banks - Global',
    'Insurance - Life', 'Insurance - Property & Casualty',
    'Insurance - Specialty', 'Insurance - Diversified',
    'Asset Management', 'Capital Markets', 'Financial Conglomerates',
    'Credit Services', 'Mortgage Finance', 'Financial Data & Stock Exchanges',
    # Tobacco
    'Tobacco',
    # Alcohol
    'Beverages - Wineries & Distilleries', 'Beverages - Breweries',
    'Distillers & Vintners', 'Brewers',
    # Weapons / Defense
    'Aerospace & Defense', 'Defense', 'Arms & Ammunition',
}

# Sectors that are always excluded
_EXCLUDED_SECTORS = {'Financial Services'}

# Keywords in company name/industry that trigger exclusion
_EXCLUDE_NAME_KEYWORDS = [
    # Financial
    'bank', 'banking', ' finance', 'financial', 'nbfc', 'insurance',
    'credit corp', 'lending', 'microfinance',
    # Tobacco
    'tobacco', 'cigarette',
    # Alcohol
    'spirits', 'distiller', 'brewery', 'winery', 'whisky', 'whiskey',
    'vodka', 'rum ', 'beer ', 'wine ', 'liquor', 'scotch', 'brandy',
    # Weapons / Defense
    'defence', 'defense', 'ordnance', 'ammunition', 'armament',
    'weapons', 'missile', 'warship', 'shipbuilder', 'aerospace defense',
]

# Explicit ticker exclusions (known financial/tobacco/alcohol)
_EXCLUDED_TICKERS_NSE = {
    # Banks
    'SBIN','HDFCBANK','ICICIBANK','AXISBANK','KOTAKBANK','INDUSINDBK',
    'BANKBARODA','CANBK','UNIONBANK','IDFCFIRSTB','AUBANK','FEDERALBNK',
    'KTKBANK','RBLBANK','EQUITASBNK','UJJIVANSFB','KARURVYSYA','DCBBANK',
    'BANDHANBNK','CSBBANK','IDBI','JKBANK','LAKSHVILAS','MAHABANK',
    # NBFCs / Financial
    'BAJFINANCE','BAJAJFINSV','CHOLAFIN','MUTHOOTFIN','MANAPPURAM',
    'ABCAPITAL','PAISALO','IIFLWAM','EDELWEISS','MOTILALOFS','NUVAMA',
    'LICHSGFIN','POONAWALLA','AAVAS','CANFINHOME','HOMEFIRST','FIVESTAR',
    'SBICARD','JMFINANCIL','KFINTECH','CDSL',
    # Insurance / AMC
    'SBILIFE','HDFCLIFE','ICICIGI','ICICIPRULI','MAXHEALTH','HDFCAMC',
    'NAUKRI',  # actually tech — keep
    # Tobacco
    'ITC','GODFRYPHLP','VSTIND',
    # Alcohol / Spirits / Industrial Alcohol
    'RADICO','MCDOWELL-N','UNITDSPR','GLOBUSSPR','TILAKNAGAR',
    'KHODAY','UTTAMSUGAR',  # distilleries
    'GAEL',  # Gujarat Ambuja Exports — has distillery/ENA division
    # Weapons / Defense / Ordnance
    'BEL','HAL','BEML','MIDHANI','GRSE','COCHINSHIP','MAZDOCK',
    'PARAS','ZEN','APOLLOMICRO','DATAPATTNS','IDEA','MTAR',
}

_EXCLUDED_TICKERS_NYSE = {
    # Banks
    'BAC','JPM','WFC','GS','MS','C','BK','STT','SCHW','NTRS',
    'USB','PNC','TFC','KEY','CFG','HBAN','RF','FITB','MTB','ZION',
    # Financial Services
    'COF','AXP','SYF','DFS','SLM','CACC','NAVI','OMF',
    'BLK','IVZ','AMG','TROW','BEN','SEIC','VRTS','LPLA',
    # Insurance
    'CB','AIG','ALL','PRU','MET','AFL','TRV','HIG','CINF','RGA',
    # Exchanges
    'CME','ICE','NDAQ','CBOE','MKTX',
    # Tobacco
    'PM','MO','BTI','VGR','TPB',
    # Alcohol
    'BUD','TAP','SAM','STZ','MGPI','DEO','BF-B','WVVI',
    # Weapons / Defense
    'LMT','RTX','NOC','GD','BA','LHX','HII','TDG','AXON',
    'KTOS','CACI','SAIC','LDOS','BAH','MOOG','HEI','TGI',
}


def _should_exclude(symbol: str, exchange: str,
                    sector: str, industry: str, name: str) -> bool:
    """Return True if this stock should be excluded by policy."""
    # Explicit ticker list
    excl = _EXCLUDED_TICKERS_NSE if exchange == 'NSE' else _EXCLUDED_TICKERS_NYSE
    if symbol in excl:
        return True
    # Sector exclusion
    if sector in _EXCLUDED_SECTORS:
        return True
    # Industry exclusion
    if industry in _EXCLUDED_INDUSTRIES:
        return True
    # Name-keyword exclusion (catches unlisted tickers)
    name_l = (name + ' ' + industry).lower()
    if any(kw in name_l for kw in _EXCLUDE_NAME_KEYWORDS):
        return True
    return False


# ── Hardcoded NSE Sector Map (fallback when yfinance returns N/A) ──
_NSE_SECTOR_FALLBACK = {
    # Technology
    'STLTECH':'Technology','HFCL':'Technology','RATEGAIN':'Technology',
    'TATAELXSI':'Technology','PERSISTENT':'Technology','COFORGE':'Technology',
    'WIPRO':'Technology','INFY':'Technology','TCS':'Technology',
    'HCLTECH':'Technology','TECHM':'Technology','MPHASIS':'Technology',
    'KPITTECH':'Technology','LTTS':'Technology','BIRLASOFT':'Technology',
    'HAPPSTMNDS':'Technology','ZENSARTECH':'Technology','MASTEK':'Technology',
    # Healthcare / Pharma
    'LAURUSLABS':'Healthcare','GRANULES':'Healthcare','SUNPHARMA':'Healthcare',
    'DRREDDY':'Healthcare','CIPLA':'Healthcare','DIVISLAB':'Healthcare',
    'LUPIN':'Healthcare','BIOCON':'Healthcare','AUROPHARMA':'Healthcare',
    'TORNTPHARM':'Healthcare','JBCHEPHARM':'Healthcare','IPCALAB':'Healthcare',
    'NATCOPHARM':'Healthcare','ASTERDM':'Healthcare','MAXHEALTH':'Healthcare',
    # Basic Materials
    'NATIONALUM':'Basic Materials','HINDALCO':'Basic Materials','SAIL':'Basic Materials',
    'SALSTEEL':'Basic Materials','TATASTEEL':'Basic Materials','JSWSTEEL':'Basic Materials',
    'NMDC':'Basic Materials','VEDL':'Basic Materials','NAVINFLUOR':'Basic Materials',
    'DEEPAKNTR':'Basic Materials','VISHNU':'Basic Materials','USHAMART':'Basic Materials',
    'MBAP':'Basic Materials','TATACHEM':'Basic Materials','GUJALKALI':'Basic Materials',
    'MBAPL':'Basic Materials',   # Madhya Bharat Agro Products — fertilizer/chemicals
    'SOLARINDS':'Basic Materials',  # Solar Industries — specialty chemicals/explosives
    'RATNAMANI':'Basic Materials',  # Ratnamani Metals — stainless steel tubes
    'LLOYDMETAL':'Basic Materials', # Lloyd Metals — steel products (if not Industrials)
    'GNFC':'Basic Materials',       # Gujarat Narmada — fertilizers & chemicals
    'GSFC':'Basic Materials',       # Gujarat State Fertilizers — fertilizers
    'AARTI':'Basic Materials',      # Aarti Industries — specialty chemicals
    'FINEORG':'Basic Materials',    # Fine Organics — specialty chemicals
    'PIIND':'Basic Materials',      # PI Industries — agrochemicals
    # Consumer Defensive
    'BAJAJCON':'Consumer Defensive','RADICO':'Consumer Defensive','GAEL':'Consumer Defensive',
    'ITC':'Consumer Defensive','HINDUNILVR':'Consumer Defensive','NESTLEIND':'Consumer Defensive',
    'BRITANNIA':'Consumer Defensive','MARICO':'Consumer Defensive','DABUR':'Consumer Defensive',
    'COLPAL':'Consumer Defensive','VBL':'Consumer Defensive','TATACONSUM':'Consumer Defensive',
    'AVANTIFEED':'Consumer Defensive',
    # Consumer Cyclical
    'SANSERA':'Consumer Cyclical','TITAN':'Consumer Cyclical','MARUTI':'Consumer Cyclical',
    'M&M':'Consumer Cyclical','EICHERMOT':'Consumer Cyclical','TVSMOTOR':'Consumer Cyclical',
    'BAJAJ-AUTO':'Consumer Cyclical','HEROMOTOCO':'Consumer Cyclical','MOTHERSON':'Consumer Cyclical',
    # Industrials
    'CUMMINSIND':'Industrials','KEI':'Industrials','POLYCAB':'Industrials',
    'ABB':'Industrials','SIEMENS':'Industrials','BEL':'Industrials',
    'ADANIPORTS':'Industrials','GESHIP':'Industrials','IRCTC':'Industrials',
    'RVNL':'Industrials','TITAGARH':'Industrials','CONCOR':'Industrials',
    'ESABINDIA':'Industrials',   # ESAB India — welding equipment
    'TIMKEN':'Industrials',      # Timken India — bearings & industrial components
    'ROLEXRINGS':'Industrials',  # Rolex Rings — metal fabrication/forging
    'GRINDWELL':'Industrials',   # Grindwell Norton — abrasives/industrial products
    'SCHAEFFLER':'Industrials',  # Schaeffler India — bearings
    'SKFINDIA':'Industrials',    # SKF India — bearings
    'ELGIEQUIP':'Industrials',   # Elgi Equipment — compressors
    'KPIL':'Industrials',        # Kalpataru — power transmission/infra
    'APLAPOLLO':'Industrials',   # APL Apollo Tubes — steel tubes
    'SINTERCOM':'Industrials',   # Sintercom — auto components
    'LLOYDMETAL':'Industrials',  # Lloyd Metals — steel/metal products
    # Utilities
    'ADANIPOWER':'Utilities','NTPC':'Utilities','POWERGRID':'Utilities',
    'NHPC':'Utilities','SJVN':'Utilities','TATAPOWER':'Utilities',
    # Energy
    'COALINDIA':'Energy','ONGC':'Energy','BPCL':'Energy','GAIL':'Energy',
    'PETRONET':'Energy','HINDPETRO':'Energy',
    # Financial Services
    'KTKBANK':'Financial Services','RBLBANK':'Financial Services',
    'FEDERALBNK':'Financial Services','ABCAPITAL':'Financial Services',
    'PAISALO':'Financial Services','AUBANK':'Financial Services',
    'CHOLAFIN':'Financial Services','MUTHOOTFIN':'Financial Services',
}

# ── Metadata Cache (company name, sector, currency) ────────────────
def _meta_cache_path(exchange: str) -> str:
    return os.path.join(DATA_DIR, f'{exchange.lower()}_meta_cache.json')

def _load_meta_cache(exchange: str) -> dict:
    path = _meta_cache_path(exchange)
    if os.path.exists(path):
        try:
            return json.load(open(path))
        except Exception:
            pass
    return {}

def _save_meta_cache(exchange: str, cache: dict):
    with open(_meta_cache_path(exchange), 'w') as f:
        json.dump(cache, f, indent=2)

def _infer_sector_from_name(name: str, symbol: str, exchange: str) -> str:
    """Last-resort sector inference from company name when yfinance returns N/A."""
    n = name.lower()
    if exchange == 'NSE' and symbol in _NSE_SECTOR_FALLBACK:
        return _NSE_SECTOR_FALLBACK[symbol]
    # Technology
    if any(k in n for k in ['tech','software','digital','data','cyber','it solut','systems','infotech','telecom']):
        return 'Technology'
    # Healthcare
    if any(k in n for k in ['pharma','health','hospital','medical','biotech','lab','life sci','therapeut']):
        return 'Healthcare'
    # Financial → will be excluded
    if any(k in n for k in ['bank','financ','insur','nbfc','credit','lending','invest','capital','asset manag']):
        return 'Financial Services'
    # Basic Materials
    if any(k in n for k in ['chemical','steel','alumin','copper','zinc','metal','mineral','agro','fertiliz','cement','alloy','coil','tube']):
        return 'Basic Materials'
    # Industrials
    if any(k in n for k in ['engineer','industri','manufactur','equipment','tools','bearing','pump','valve','weld','fabricat','infra','construct']):
        return 'Industrials'
    # Energy
    if any(k in n for k in ['energy','power','coal','oil','gas','petro','solar','wind','renew']):
        return 'Energy'
    # Utilities
    if any(k in n for k in ['electric','utility','utilities','transmiss','distribution','water supply']):
        return 'Utilities'
    # Consumer
    if any(k in n for k in ['consumer','retail','food','beverage','textile','apparel','fashion','fmcg','packag']):
        return 'Consumer Defensive'
    # Alcohol/Tobacco → will be excluded
    if any(k in n for k in ['tobacco','cigarette','spirits','distill','brewery','whisky','vodka','wine','liquor','beer']):
        return 'Consumer Defensive'  # sector — but will be excluded by industry check
    return 'N/A'


def _fetch_meta_for_missing(symbols: list, exchange: str, cache: dict) -> dict:
    """Fetch name/sector/currency for symbols not yet in cache. Uses individual calls
    but only for NEW or uncached symbols — amortised over many runs."""
    suffix = '.NS' if exchange == 'NSE' else ''
    missing = [s for s in symbols if s not in cache]
    if not missing:
        return cache

    def _one_meta(sym):
        ticker = sym + suffix if '.' not in sym else sym
        fallback_sector  = _NSE_SECTOR_FALLBACK.get(sym, 'N/A') if exchange == 'NSE' else 'N/A'
        default_currency = 'INR' if exchange == 'NSE' else 'USD'
        try:
            info     = yf.Ticker(ticker).info
            name     = info.get('longName', info.get('shortName', sym))
            sector   = info.get('sector') or ''
            industry = info.get('industry', '')
            currency = info.get('currency', default_currency)

            # If yfinance returned N/A sector, use fallback map then name inference
            if not sector or sector == 'N/A':
                sector = fallback_sector
            if not sector or sector == 'N/A':
                sector = _infer_sector_from_name(name, sym, exchange)

            return sym, {'name': name, 'sector': sector,
                         'industry': industry, 'currency': currency}
        except Exception:
            return sym, {'name': sym, 'sector': fallback_sector,
                         'industry': '', 'currency': default_currency}

    with ThreadPoolExecutor(max_workers=4) as pool:
        for sym, meta in pool.map(_one_meta, missing):
            cache[sym] = meta
            time.sleep(0.05)

    _save_meta_cache(exchange, cache)
    return cache


# ── Batch Price History Download ────────────────────────────────────
def _batch_download_monthly(symbols: list, exchange: str,
                             batch_size: int = 100) -> dict:
    """
    Download 5 years of MONTHLY OHLCV for all symbols in batches of 100.
    Returns {full_ticker: DataFrame} — far fewer API calls than per-stock fetches.
    """
    suffix = '.NS' if exchange == 'NSE' else ''
    full_tickers = [s + suffix if '.' not in s else s for s in symbols]
    sym_map = {s + suffix if '.' not in s else s: s for s in symbols}

    price_data = {}  # symbol -> DataFrame

    for i in range(0, len(full_tickers), batch_size):
        batch = full_tickers[i:i + batch_size]
        attempt = 0
        while attempt < 3:
            try:
                df = yf.download(
                    batch, period='5y', interval='1mo',
                    progress=False, auto_adjust=True,
                    group_by='ticker' if len(batch) > 1 else None,
                )
                if df.empty:
                    break

                for ft in batch:
                    sym = sym_map[ft]
                    try:
                        if len(batch) == 1:
                            stock_df = df
                        else:
                            # Multi-ticker download returns MultiIndex columns
                            if ft in df.columns.get_level_values(0):
                                stock_df = df[ft].copy()
                            else:
                                continue
                        stock_df = stock_df.dropna(subset=['Close'])
                        if len(stock_df) >= 14:
                            price_data[sym] = stock_df
                    except Exception:
                        continue
                break  # success — move to next batch
            except Exception:
                attempt += 1
                time.sleep(2 ** attempt)  # exponential backoff

        time.sleep(1.5)  # polite pause between batches

    return price_data


# ── Score from pre-downloaded data (no yfinance calls) ─────────────
def _score_from_data(symbol: str, exchange: str,
                     monthly_df: pd.DataFrame, meta: dict) -> dict | None:
    """Calculate all 5 indicators from pre-downloaded DataFrame. Zero API calls."""
    try:
        # ── Exclusion policy: Financial / Tobacco / Alcohol ───────────
        if _should_exclude(symbol, exchange,
                           meta.get('sector', ''),
                           meta.get('industry', ''),
                           meta.get('name', symbol)):
            return None
        res = {
            '12_1_momentum': indicator_price_momentum(monthly_df),
            'rsi':           indicator_rsi(monthly_df),
            'macd':          indicator_macd(monthly_df),
            '52_week_high':  indicator_52week_high(monthly_df),
            'ma_momentum':   indicator_ma_momentum(monthly_df),
        }
        for key in res:
            res[key].update(INDICATOR_META[key])
            res[key]['weight'] = INDICATOR_WEIGHTS[key]
            res[key]['weighted_score'] = round(
                res[key]['score'] * INDICATOR_WEIGHTS[key], 2)

        final_score = round(
            sum(res[k]['score'] * INDICATOR_WEIGHTS[k] for k in res), 1)

        if final_score < 81:
            return None

        # ── Extra factor-specific scores (NOT part of final_score / the
        #    81+ screening bar above) — computed only for stocks that
        #    already cleared the base screen, so this never changes who
        #    makes the elite pool. These exist so S12/S13/S14 can select
        #    by their OWN named academic factor instead of proxying off
        #    RSI/MACD/52-week-high, which was causing them to pick
        #    identical portfolios. weight=0 keeps them out of final_score.
        for key, fn in (
            ('intermediate_momentum', indicator_intermediate_momentum),
            ('consistency',           indicator_momentum_consistency),
            ('confluence',            indicator_multi_horizon_confluence),
        ):
            v = fn(monthly_df)
            v['weight'] = 0
            v['weighted_score'] = 0
            v.setdefault('short', key)
            res[key] = v

        suffix = '.NS' if exchange == 'NSE' else ''
        ticker   = symbol + suffix if '.' not in symbol else symbol
        cat_map  = _NSE_CATEGORY_MAP if exchange == 'NSE' else _NYSE_CATEGORY_MAP
        cur_price = float(monthly_df['Close'].iloc[-1])

        ind_snap = {k: {'score': v['score'], 'signal': v.get('signal', ''),
                        'weighted_score': v['weighted_score'],
                        'short': v.get('short', k),
                        'value': v.get('value'),
                        'meets_9_of_12': v.get('meets_9_of_12'),
                        'all_positive': v.get('all_positive'),
                        'returns': v.get('returns')} for k, v in res.items()}

        if final_score >= 80:
            mom_class, mom_color = 'Very Strong', '#00c853'
            rec = 'High momentum. Strong candidate for long-term position.'
        else:
            mom_class, mom_color = 'Strong', '#64dd17'
            rec = 'Good momentum. Consider building a position.'

        # Use fallback sector map if metadata returned N/A
        sector = meta.get('sector') or ''
        if (not sector or sector == 'N/A') and exchange == 'NSE':
            sector = _NSE_SECTOR_FALLBACK.get(symbol, 'N/A')

        return {
            'ticker':         ticker,
            'symbol':         symbol,
            'company_name':   meta.get('name', symbol),
            'sector':         sector,
            'category':       cat_map.get(symbol, 'Unknown'),
            'currency':       meta.get('currency', 'INR' if exchange == 'NSE' else 'USD'),
            'current_price':  cur_price,
            'final_score':    final_score,
            'momentum_class': mom_class,
            'momentum_color': mom_color,
            'recommendation': rec,
            'indicators':     ind_snap,
        }
    except Exception:
        return None


def _scan_one(symbol: str, exchange: str) -> dict | None:
    """Scan a single stock; return compact result dict or None."""
    suffix = '.NS' if exchange == 'NSE' else ''
    ticker = symbol + suffix if '.' not in symbol else symbol

    cat_map = _NSE_CATEGORY_MAP if exchange == 'NSE' else _NYSE_CATEGORY_MAP
    category = cat_map.get(symbol, 'Unknown')

    try:
        data = calculate_all_indicators(ticker)
        score = data['final_score']

        # Apply exclusion policy
        sector   = data['stock'].get('sector', '')
        industry = data['stock'].get('industry', '')
        name     = data['stock'].get('company_name', symbol)
        if _should_exclude(symbol, exchange, sector, industry, name):
            return None

        if score >= 81:
            ind_snap = {}
            for k, v in data['indicators'].items():
                ind_snap[k] = {
                    'score': v['score'],
                    'signal': v.get('signal', ''),
                    'weighted_score': v['weighted_score'],
                    'short': v.get('short', k),
                }
            return {
                'ticker':        ticker,
                'symbol':        symbol,
                'company_name':  data['stock']['company_name'],
                'sector':        data['stock']['sector'],
                'category':      category,
                'currency':      data['stock']['currency'],
                'current_price': data['stock']['current_price'],
                'final_score':   score,
                'momentum_class': data['momentum_class'],
                'momentum_color': data['momentum_color'],
                'recommendation': data['recommendation'],
                'indicators':    ind_snap,
            }
        return None
    except Exception:
        return None


# ── Main Screener ──────────────────────────────────────────────────

def run_screener(exchange: str) -> dict:
    """
    Scan all stocks for the given exchange, keep score >= 81.

    Strategy: batch-download ALL monthly price history in ~9 API calls
    (100 stocks per batch), then score every stock locally — no per-stock
    yfinance calls. Company metadata is cached and only refreshed for new
    symbols. This makes the screener consistent regardless of run frequency.

    Saves result to data/<exchange>_screener.json.
    Thread-safe — only one concurrent run per exchange.
    """
    if not _locks[exchange].acquire(blocking=False):
        return load_screener(exchange) or {'error': 'Screener already running'}

    stocks   = NSE_STOCKS if exchange == 'NSE' else NYSE_STOCKS
    old_data = load_screener(exchange)
    old_count = len(old_data.get('stocks', [])) if old_data else 0

    p = _progress[exchange]
    p.update({
        'status':  'running',
        'started': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'done': 0, 'total': len(stocks),
        'found': 0, 'current': 'Downloading price history…', 'errors': 0,
    })

    results = []

    try:
        # ── Step 1: Batch-download all monthly price history ──────────
        # ~9 API calls for NYSE (869 stocks / 100 per batch)
        # ~5 API calls for NSE (494 stocks / 100 per batch)
        p['current'] = 'Batch downloading price history (this takes ~2 min)…'
        price_data = _batch_download_monthly(stocks, exchange, batch_size=100)

        # ── Step 2: Load / refresh metadata cache ─────────────────────
        p['current'] = 'Refreshing company metadata…'
        meta_cache = _load_meta_cache(exchange)
        # Only fetch metadata for stocks that have price data but no cache entry
        symbols_with_data = list(price_data.keys())
        meta_cache = _fetch_meta_for_missing(symbols_with_data, exchange, meta_cache)

        # ── Step 3: Score every stock from local data (zero API calls) ─
        p['current'] = 'Scoring stocks…'
        p['total']   = len(price_data)

        for sym, monthly_df in price_data.items():
            p['done'] += 1
            p['current'] = sym
            meta = meta_cache.get(sym, {})
            res  = _score_from_data(sym, exchange, monthly_df, meta)
            if res:
                results.append(res)
                p['found'] += 1

        # ── Step 4: Sort & rank ────────────────────────────────────────
        results.sort(key=lambda x: x['final_score'], reverse=True)
        for i, r in enumerate(results, 1):
            r['rank'] = i

        # ── Step 5: Safety guard ───────────────────────────────────────
        # If we got suspiciously few results, the batch download likely
        # failed partially — keep the previous good data.
        min_threshold = max(5, int(old_count * 0.40)) if old_count > 0 else 3
        if len(results) < min_threshold:
            p.update({'status': 'done', 'found': old_count,
                      'current': f'Kept previous results ({old_count} stocks) — new scan returned too few'})
            return old_data if old_data else {'stocks': [], 'high_momentum_count': 0}

        # ── Step 6: Category breakdown & save ─────────────────────────
        cat_counts = {}
        for r in results:
            c = r.get('category', 'Unknown')
            cat_counts[c] = cat_counts.get(c, 0) + 1

        output = {
            'exchange':            exchange,
            'currency':            'INR' if exchange == 'NSE' else 'USD',
            'last_run':            datetime.now().strftime('%Y-%m-%d %H:%M'),
            'next_run':            (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d'),
            'total_scanned':       len(stocks),
            'stocks_with_data':    len(price_data),
            'high_momentum_count': len(results),
            'errors_count':        p['errors'],
            'category_breakdown':  cat_counts,
            'stocks':              results,
        }

        with open(_screener_path(exchange), 'w') as f:
            json.dump(output, f, indent=2)

        p.update({'status': 'done', 'current': f'Complete — {len(results)} elite stocks found'})
        return output

    except Exception as e:
        p['status'] = 'error'
        return old_data if old_data else {'error': str(e)}

    finally:
        _locks[exchange].release()


def run_screener_background(exchange: str) -> bool:
    """Launch screener in a daemon thread. Returns False if already running."""
    if _progress[exchange]['status'] == 'running':
        return False
    t = threading.Thread(target=run_screener, args=(exchange,), daemon=True)
    t.start()
    return True
