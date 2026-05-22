"""
WORDF v2 — Ultra-Reliable Stock Signal Engine
==============================================
Reads NSE/BSE corporate announcements from Google Sheets,
applies context-aware keyword matching, text quality scoring,
compliance filing detection, and historical pattern analysis
to generate high-confidence BUY/SELL signals.

v2 Changes:
- Context-aware acquisition matching (no more false positives)
- Compliance filing detection (filters regulatory noise)
- Text quality scoring (rejects short/vague filings)
- Raised confidence threshold to 80% (was 60%)
- NCLT approval at score=8 (highest priority)
- Added EBITDA, research upgrade, demerger, govt order patterns
- Input/output deduplication
- BSE short-text handler
- Historical pattern analysis with yfinance price validation

Previous version (v1) is preserved in git history.
"""

import re
import gspread
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials
import pytz

# ── Market Intelligence + Alerts ──
try:
    from market_utils import enrich_signal, check_freshness, load_pattern_scores
    MARKET_UTILS_OK = True
except ImportError:
    MARKET_UTILS_OK = False

try:
    from alerts import send_alert, send_summary
    ALERTS_OK = True
except ImportError:
    ALERTS_OK = False

# =============================
# GOOGLE AUTH
# =============================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

def get_client():
    creds = ServiceAccountCredentials.from_json_keyfile_name(
        "credentials.json", scope
    )
    return gspread.authorize(creds)

# =============================
# IST TIME
# =============================
def get_ist_time():
    ist = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")

# =============================
# CONFIG
# =============================
SHEET_ID = "1EQAhrCWmOzDD6VhVig4f3AffWMVZmrsrZKkgUc6h6WQ"

SOURCE_WEIGHT = {
    "nse": 5,
    "bse": 5,
}

# =============================
# CONFIDENCE THRESHOLDS (RAISED FOR RELIABILITY)
# =============================
BUY_CONF_THRESHOLD  = 80
SELL_CONF_THRESHOLD = 80

# =============================
# SCORE CALIBRATION (RECALIBRATED)
#
# With BUY_100_SCORE = 45:
# One STRONG_BUY (+6) × weight 5 = 30 weighted  → 67% conf  (NOT shown — below 80%)
# One NCLT_APPROVAL (+8) × weight 5 = 40        → 89% conf  (SHOWN ✅)
# Two STRONG_BUY = 60 weighted                   → 100% conf (SHOWN ✅)
# One STRONG_BUY + money_score 3 = (6+3)×5 = 45  → 100% conf (SHOWN ✅)
# One MED_BUY (+3) × weight 5 = 15 weighted      → 33% conf  (NOT shown)
#
# This means a filing only crosses 80% when:
#   - NCLT approval or resolution plan (score=8), OR
#   - One strong keyword + significant deal value, OR
#   - Two independent strong keywords in the same text
# =============================
BUY_100_SCORE  = 45
SELL_100_SCORE = -45

# =============================
# KEYWORD ENGINE — BUY PATTERNS
#
# Each entry: (regex_pattern, score, label)
# Pattern is regex for flexibility:
#   - Plural/verb variants: acqui(re|res|red|ring|sition)
#   - Word-order flexibility: "order.{0,20}received"
#   - Optional words: "letter of (award|intent)"
#   - Boundaries: \b prevents partial matches
# =============================

BUY_PATTERNS = [

    # ── HIGHEST PRIORITY (+8) — NCLT / Resolution ───────────────────────
    (r'\bnclt\s+(approv(al|es?|ed)|order\s+for|confirms?|sanctions?)\b',  8, "nclt approval"),
    (r'\bresolution\s+plan\s+(approved|confirmed|accepted|sanctioned)\b', 8, "resolution plan approved"),
    (r'\bnclt\s+order\s+(in\s+fav(ou?r)|approv)\b',                       8, "nclt order in favour"),

    # ── STRONG BUY (+6) ─────────────────────────────────────────────────
    # Orders & Contracts
    (r'\bl1\s*bidder\b',                                                    6, "l1 bidder"),
    (r'\blowest\s+bidder\b',                                                6, "lowest bidder"),
    (r'\bletter\s+of\s+award\b',                                            6, "letter of award"),
    (r'\bloa\s+(received|issued|awarded)\b',                                6, "loa received/issued"),
    (r'\bwork\s+order\s+(received|awarded|secured|worth)\b',               6, "work order received"),
    (r'\bcontract\s+(awarded|secured|signed|worth|received)\b',            6, "contract awarded/secured"),
    (r'\border\s+(secured|received|awarded|bagged|won)\b',                 6, "order secured/received"),
    (r'\border(s)?\s+(worth|valued?\s+at|of\s+rs|of\s+inr)\b',           6, "order worth ₹"),
    (r'\b(large|mega|significant|major|repeat)\s+order\b',                6, "large/mega/significant order"),
    (r'\border\s+intak(e|es)\b',                                           6, "order intake"),
    (r'\bexecut(e|ed|ing)\s+(a\s+)?share\s+purchase\s+agreement\b',       6, "share purchase agreement"),

    # Acquisition — CONTEXT-AWARE (replaces old over-broad \bacquisition\b)
    (r'\bacquisition\s+of\s+(company|business|plant|facility|brand|division|subsidiary|unit|operations?|controlling\s+interest|majority)\b', 6, "business acquisition"),
    (r'\bacquir(e[sd]?|ing)\s+(a\s+)?(company|business|plant|facility|brand|division|subsidiary|unit|controlling\s+interest)\b', 6, "business acquisition"),
    (r'\bacquir(e[sd]?|ing)\s+\d+\s*%\s*(stake|equity|interest|shareholding)\s+in\b', 6, "stake acquisition"),
    (r'\bacquisition\s+(deal|agreement|completed|announced|worth|valued|target)\b', 6, "acquisition deal"),

    (r'\b49\s*%\s*(equity\s+)?stake\b',                                    6, "stake acquisition"),

    # Financial Milestones
    (r'\brecord\s+(profit|revenue|sales|earnings|ebitda)\b',              6, "record profit/revenue"),
    (r'\bhighest\s+ever\s+(profit|revenue|sales)\b',                      6, "highest ever profit"),
    (r'\ball.?time\s+high\s+(profit|revenue|sales)\b',                    6, "all-time high"),
    (r'\bprofit\s+(doubles?|triples?|surges?|jumps?|soars?)\b',           6, "profit doubles/surges"),
    (r'\bnet\s+profit\s+(surges?|jumps?|rises?|up)\b',                    6, "net profit surge"),
    (r'\bebitda\s+(surges?|jumps?|rises?|up|grows?)\b',                   6, "ebitda surge"),
    (r'\bbeat(s|ing)?\s+(estimates?|expectations?|consensus)\b',          6, "beat estimates"),

    # Corporate Actions
    (r'\bbuy\s*back\b|\bshare\s+buy\s*back\b',                            6, "buyback"),
    (r'\bbonus\s+(issue|shares?)\b',                                       6, "bonus issue/shares"),
    (r'\bstock\s+split\b|\bshare\s+split\b',                              6, "stock/share split"),
    (r'\brights?\s+issue\b',                                               6, "rights issue"),

    # Debt & Promoter
    (r'\bdebt[\s-]?free\b|\bzero\s+debt\b',                               6, "debt-free"),
    (r'\bdelevera(ge|ging|ged)\b|\bdebt\s+(repaid|cleared|fully\s+paid)\b', 6, "deleveraging"),
    (r'\bpromoter\s+(increases?|buys?|acquires?|purchased?)\s+(stake|shares?)\b', 6, "promoter buys"),
    (r'\bopen\s+market\s+(purchase|buy)\b',                               6, "open market purchase"),

    # Turnaround
    (r'\bturnaround\b',                                                    6, "turnaround"),
    (r'\breturns?\s+to\s+profit\b|\bback\s+in\s+black\b',                6, "returns to profit"),
    (r'\bvalue\s+unlocking\b|\bstrategic\s+divestment\b',                 6, "value unlocking"),

    # EBITDA Growth
    (r'\bebitda\s+(grow(th|s|n|ing)|improv(es?|ed|ing|ement)|expan(sion|ds?|ded|ding))\b', 6, "ebitda growth"),
    (r'\bebitda\s+margin\s+(expan(sion|ds?)|improv(es?|ed)|up)\b',        6, "ebitda margin expansion"),

    # Research / Brokerage Upgrades
    (r'\b(initiates?\s+coverage|target\s+price)\s+(with\s+)?(buy|outperform|overweight)\b', 6, "research upgrade"),
    (r'\b(broker(age)?|analyst)\s+(upgrade[sd]?|initiat(es?|ed|ion))\b',  5, "brokerage upgrade"),

    # Demerger / Spin-off (value unlocking)
    (r'\b(de-?merger|spin.?off|hive.?off)\s+(approv(al|ed)|of|plan|scheme)\b', 6, "demerger/spin-off"),

    # Government / Defence Orders (high reliability)
    (r'\b(government|defence|defense|ministry|railway|nhai|nhpc|isro)\s+(order|contract)\b', 6, "govt/defence order"),

    # ── MEDIUM BUY (+3 to +5) ──────────────────────────────────────────
    # Defence contract with value mentioned
    (r'\b(defence|defense)\s+(order|contract)\s+(worth|valued?|of\s+rs)\b', 5, "defence contract value"),

    # Target price
    (r'\btarget\s+price\s+(of|at|set|raised?\s+to)\s+rs\b',              4, "target price set"),

    # Capacity & Capex
    (r'\bcapacity\s+expan(sion|d|ding)\b',                                3, "capacity expansion"),
    (r'\b(brownfield|greenfield)\s+expan(sion|d)\b',                      3, "brownfield/greenfield expansion"),
    (r'\bnew\s+(manufacturing\s+)?plant\b',                                3, "new plant"),
    (r'\bcapex\s+(of|plan|worth|investment)\b',                           3, "capex"),
    (r'\bcapital\s+expenditure\s+(of|plan|worth)\b',                      3, "capital expenditure"),
    (r'\bcapacity\s+addition\b',                                           3, "capacity addition"),

    # Deals & Alliances
    (r'\bjoint\s+venture\b|\bjv\s+(agreement|formed|signed)\b',           3, "joint venture"),
    (r'\bstrategic\s+partner(ship)?\b',                                    3, "strategic partnership"),
    (r'\bcollabor(ation|ate|ating)\s+(agreement|with)\b',                 3, "collaboration"),
    (r'\btechnology\s+(transfer|agreement|licens(e|ing))\b',              3, "technology agreement"),
    (r'\bdefinitive\s+agreement\s+(signed|executed)\b',                   3, "definitive agreement"),
    (r'\bmerger\s+(agreement|approved|completed)\b',                      3, "merger"),
    (r'\btakeover\s+offer\b|\bopen\s+offer\b',                            3, "takeover/open offer"),

    # Financial Performance
    (r'\bearnings?\s+beat\b',                                              3, "earnings beat"),
    (r'\brevenue\s+(growth|grew|rises?|up)\b',                             3, "revenue growth"),
    (r'\bmargin\s+expan(sion|d|ding)\b',                                   3, "margin expansion"),
    (r'\bstrong\s+order\s+book\b|\border\s+(book\s+(grows?|grew)|pipeline)\b', 3, "strong order book"),
    (r'\border\s+inflow\b',                                                 3, "order inflow"),

    # Fundraising
    (r'\bqip\b|\bqualified\s+institutional\s+placement\b',                3, "qip"),
    (r'\bpreferential\s+allotment\b',                                      3, "preferential allotment"),
    (r'\bncd\s+(issue|allotment|raised)\b|\bnon.?convertible\s+(debt|securities|debenture)\s+(issued?|allotted?)\b', 3, "ncd issue"),
    (r'\brights?\s+entitlement\b',                                          3, "rights entitlement"),
    (r'\bfund\s*(raise|raising|raised)\b|\bprivate\s+placement\b',        3, "fundraise/private placement"),
    (r'\bipo\s+(opens?|subscri|listed?)\b',                                3, "ipo"),

    # Launch (medium-buy)
    (r'\b(launches?|launched|launching)\s+(india.?s?\s+first|world.?s?\s+first)\b', 3, "launches India's/world's first"),
    (r'\bsingle.?window\s+approval\b',                                     3, "single-window approval system"),

    # PLI Scheme
    (r'\bpli\s+(scheme|benefit|incentive|approval|eligible)\b',           4, "pli scheme"),

    # Institutional Buying
    (r'\b(fii|dii|fpi|mutual\s+fund)\s+(buy|bought|increase[sd]?\s+stake|added)\b', 4, "institutional buying"),

    # Special Dividend
    (r'\bspecial\s+dividend\b',                                           4, "special dividend"),

    # ── LIGHT BUY (+1) ──────────────────────────────────────────────────
    (r'\bmemorandum\s+of\s+understanding\b|\bmou\s+(signed|executed|entered)\b', 1, "mou signed"),
    (r'\bletter\s+of\s+intent\b|\bloi\s+(signed|executed)\b',             1, "letter of intent"),
    (r'\bnew\s+product\s+(launch|launched)\b|\bproduct\s+(launch|launched)\b', 1, "product launch"),
    (r'\bnew\s+vertical\b|\bmarket\s+expan(sion|d)\b',                    1, "market expansion"),
    (r'\bexport\s+(order|contract)\b',                                     1, "export order"),
    (r'\bdistribution\s+agreement\b|\btie.?up\b',                         1, "distribution agreement/tie-up"),
    (r'\bempanell?ed\b|\bregistered\s+vendor\b',                           1, "empanelled/registered vendor"),
    (r'\bappointment\s+of\s+(managing|joint\s+managing|executive)\s+director\b', 1, "appointment of md/jmd"),
    (r'\bnew\s+credit\s+rating\b|\bcredit\s+rating.{0,20}(assigned|obtained|received)\b', 1, "new credit rating"),
]

SELL_PATTERNS = [

    # ── STRONG SELL (-6) ────────────────────────────────────────────────
    # SEBI Actions
    (r'\bsebi\s+(order|action|notice|penalty|ban|restraint|investigation)\s+(against|on|to)\b', -6, "sebi action against"),
    (r'\bsebi\s+show\s+cause\s+notice\b',                                 -6, "sebi show cause"),
    (r'\bsebi\s+investigation\b',                                          -6, "sebi investigation"),

    # Fraud & Accounting
    (r'\bfraud\s+(detected|alleged|committed|found)\b',                   -6, "fraud detected/alleged"),
    (r'\baccounting\s+irregularities?\b',                                 -6, "accounting irregularities"),
    (r'\bforensic\s+(audit|investigation)\b',                             -6, "forensic audit/investigation"),
    (r'\bmisappropriat(e|ion|ing)\b|\bembezzl(e|ement|ing)\b',           -6, "misappropriation/embezzlement"),
    (r'\bfalsif(y|ied|ication)\s+of\s+(accounts?|records?|books?)\b',    -6, "falsification of accounts"),

    # Insolvency & Default
    (r'\bnclt\s+admits?\b|\binsolvency\s+petition\s+admit(ted)?\b',      -6, "nclt admits insolvency"),
    (r'\bcorporate\s+insolvency\s+resolution\b|\bcirp\b',                 -6, "cirp/insolvency"),
    (r'\bdefault\s+on\s+(ncd|debenture|loan|bond|repayment)\b',          -6, "default on ncd/loan"),
    (r'\bloan\s+default\b|\bpayment\s+default\b',                        -6, "loan/payment default"),
    (r'\bwilful\s+default(er)?\b',                                        -6, "wilful defaulter"),
    (r'\baccount\s+classified\s+(as\s+)?npa\b|\bdeclared\s+(as\s+)?npa\b|\bnpa\s+classification\b', -6, "npa"),
    (r'\bfirst\s+meeting\s+of\s+committee\s+of\s+creditors\b|\bcoc\s+meeting\b', -6, "committee of creditors"),

    # Auditor Red Flags
    (r'\bauditor\s+(resign(s|ed|ation)|quit(s|ting))\b',                  -6, "auditor resignation"),
    (r'\bgoing\s+concern\s+(doubt|qualif|disclaim)\b',                    -6, "going concern doubt"),
    (r'\b(qualified|adverse|disclaim)\w*\s+(audit\s+)?opinion\b',        -6, "qualified/adverse audit opinion"),

    # Pledge Invocation
    (r'\bpledge\s+(invok|trigger)\w+\b|\bpledged\s+shares\s+invok\w+\b', -6, "pledge invoked"),
    (r'\bmargin\s+call\s+trigger\w+\b',                                   -6, "margin call triggered"),
    (r'\bpromoter\s+pledge\s+(rises?\s+sharply|increases?\s+significantly)\b', -6, "promoter pledge rises"),

    # ── MEDIUM SELL (-3) ────────────────────────────────────────────────
    # Rating Downgrades
    (r'\b(credit\s+)?rating\s+downgrad\w+\b',                            -3, "rating downgraded"),
    (r'\boutlook\s+revis\w+\s+to\s+(negative|watch)\b',                  -3, "outlook revised negative"),
    (r'\bplaced\s+on\s+(credit\s+)?watch\s+(negative|developing)\b',     -3, "placed on watch negative"),

    # Financial Deterioration
    (r'\bloss\s+widen(s|ed|ing)\b',                                       -3, "loss widens"),
    (r'\bnet\s+loss\s+(report|record|post)\w+\b',                         -3, "net loss reported"),
    (r'\bearnings?\s+miss\b',                                              -3, "earnings miss"),
    (r'\bprofit\s+(falls?|declin\w+|drops?)\s+(sharply|significantly)?\b', -3, "profit falls"),
    (r'\brevenue\s+(declin\w+|falls?|drops?|contracts?)\b',               -3, "revenue declines"),
    (r'\bmargin\s+contracts?\b|\bebitda\s+(declin\w+|falls?|drops?)\b',  -3, "margin/ebitda declines"),

    # Operations
    (r'\bproduction\s+(halt|shutdown|suspend\w+|stopped)\b',              -3, "production halt/shutdown"),
    (r'\bplant\s+(shut\s*down|closed?|suspend\w+)\b',                    -3, "plant shutdown"),
    (r'\bfactory\s+fire\b|\bforce\s+majeure\s+(declar\w+|invok\w+)\b',  -3, "factory fire/force majeure"),
    (r'\boperations?\s+(suspend\w+|halt\w+|stopped)\b',                  -3, "operations suspended"),

    # Governance & Key Person Risk
    (r'\bgovernance\s+(concern|issue|lapse)\b',                           -3, "governance concern"),
    (r'\bpromoter\s+conflict\b|\bboard\s+disput(e|ing)\b',               -3, "promoter conflict/board dispute"),
    (r'\b(ceo|md|cfo|coo|chairman)\s+resign(s|ed|ation)\b',             -3, "ceo/md/cfo resigns"),
    (r'\bkey\s+management\s+resignation\b|\bmass\s+resignation\b',       -3, "key management resignation"),
    (r'\bindependent\s+director\s+resign(s|ed|ation)\b',                 -3, "independent director resigns"),

    # Raids & Attachments
    (r'\b(ed|cbi|income\s*tax)\s+raid\b|\bsearch\s+and\s+seizure\b',    -3, "ed/cbi/it raid"),
    (r'\bassets?\s+attach\w+\b|\battachment\s+order\b',                  -3, "assets attached"),

    # Resignation of KMP
    (r'\bresignation\s+of\s+(director|kmp|smp|company\s+secretary|compliance\s+officer)\b', -3, "resignation of director/kmp"),

    # ── LIGHT SELL (-1) ─────────────────────────────────────────────────
    (r'\bpromoter\s+(sells?|sold|reduc\w+|offload\w+)\s+(shares?|stake)\b', -1, "promoter sells shares"),
    (r'\bbulk\s+deal\s+(sell|sold|offload)\b',                            -1, "bulk deal sold"),
    (r'\bmargin\s+pressure\b',                                            -1, "margin pressure"),
    (r'\bguidance\s+(cut|lower\w+|revis\w+\s+down)\b',                  -1, "guidance cut"),
    (r'\bpenalty\s+(impos\w+|levied?)\b',                                -1, "penalty imposed"),
    (r'\bfine\s+(impos\w+|levied?)\s+by\b',                             -1, "fine imposed by"),
    (r'\blitigation\s+(pending|filed|against)\b',                        -1, "litigation"),
    (r'\b(legal|regulatory|show\s*cause|demand)\s+notice\s+(receiv\w+|issu\w+)\b', -1, "legal/regulatory notice"),
    (r'\btax\s+demand\s+(rais\w+|receiv\w+|issu\w+)\b',                -1, "tax demand"),
    (r'\bcontingent\s+liability\b',                                      -1, "contingent liability"),
]

# ── IGNORE PATTERNS (EXPANDED — 50+ patterns) ──────────────────────────
# Routine / non-material filings — skip entirely (zero score)
IGNORE_PATTERNS = [
    r'\bboard\s+meeting\s+(intimation|scheduled|notice)\b',
    r'\bpostal\s+ballot\b',
    r'\b(agm|egm)\s+(notice|on|scheduled)\b',
    r'\binvestor\s+meet\b|\banalyst\s+meet\b|\bearnings?\s+(call|conference\s+call)\b',
    r'\btrading\s+window\s+(clos\w+|open\w+|shall)\b',
    r'\bclarification\s+(sought|submitted|given)\b',
    r'\bnewspaper\s+publication\b|\bnewspaper\s+advertisement\b',
    r'\bsaksham\s+niveshak\b',
    r'\bchange\s+of\s+(registered\s+)?address\b',
    r'\bbook\s+closure\b',
    r'\brecord\s+date\s+for\s+dividend\b',
    r'\b(interim|final)\s+dividend\b|\bdividend\s+payment\b|\bdividend\s+of\s+rs\b',
    r'\bloss\s+of\s+share\s+certificate\b|\bduplicate\s+share\s+certificate\b',
    r'\btransmission\s+of\s+shares\b',
    r'\breg(ulation)?\s*(74|40|7)\b',
    r'\blarge\s+corporate\s+(disclosure|criteria|entity)\b',
    r'\bformat\s+of\s+(initial|annual)\s+disclosure\b',
    r'\bsecretarial\s+compliance\s+report\b',
    r'\bmonthly\s+reporting\b',
    r'\bchange\s+in\s+kmp\b',
    r'\bscrutinizer\s+report\b|\bvoting\s+result\b|\be-?voting\b',
    r'\bemployee\s+stock\s+option\b|\besop\b',
    r'\bpublic\s+notice\b',
    r'\bconference\s+call\s+(invitation|scheduled)\b',
    r'\bnot\s+a?\s+large\s+corporate\b|\bdoes\s+not\s+fall\s+under\b',
    r'\bnon.?applicability\b',
    r'\besg\s+rating\b',
    r'\bintimation\s+of\s+postal\s+ballot\b',
    r'\btenure\s+of\b',
    r'\binternal\s+reorgani[sz]ation\b',
    r'\bpost\s+offer\s+advertisement\b',

    # ── ROUTINE SHARE / REGULATORY FILINGS (v2 additions) ───────────────
    r'\bacquisition\s+of\s+shares?\s+(under|pursuant|in\s+terms|by)\b',
    r'\btransfer\s+of\s+(shares?|equity|securities)\b',
    r'\breclassification\s+of\s+(promoter|shareholding)\b',
    r'\bsubmission\s+of\s+(annual|quarterly|half|compliance)\b',
    r'\bchange\s+in\s+(management|directorate)\b',
    r'\bnotice\s+of\s+(extra\s*ordinary|general)\s+meeting\b',
    r'\bstatement\s+of\s+investor\s+complaints?\b',
    r'\bunder\s+regulation\s+\d+\b',
    r'\bpursuant\s+to\s+(regulation|sebi|rule|clause)\b',
    r'\bcode\s+of\s+conduct\b',
    r'\bannual\s+return\b',
    r'\brelated\s+party\s+transactions?\b',
    r'\bfinancial\s+results?\s+(for|of|quarter|year|period)\b',
    r'\b(initial|continual)\s+disclosure\b',
    r'\b(inter-?se|inter\s+se)\s+transfer\b',
    r'\bmaterial\s+subsidiary\b',
    r'\bcompliance\s+(with|under|certificate|report|officer)\b',
    r'\bdisclosure\s+(under|pursuant|of\s+related)\b',
    r'\bcertificate\s+(from|of|under)\b',
    r'\bshare\s+certificate\b',
    r'\bformation\s+of\s+committee\b',
    r'\bcomposition\s+of\s+(board|committee)\b',
    r'\bpolicy\s+on\s+(related|determination|remuneration)\b',
    r'\bdetails\s+of\s+familiarization\b',
    r'\bshareholding\s+pattern\b',
    r'\bcorporate\s+governance\s+report\b',
]

# Precompile all patterns once at startup for speed
_BUY_COMPILED  = [(re.compile(p, re.IGNORECASE), sc, lbl) for p, sc, lbl in BUY_PATTERNS]
_SELL_COMPILED = [(re.compile(p, re.IGNORECASE), sc, lbl) for p, sc, lbl in SELL_PATTERNS]
_IGNORE_COMPILED = [re.compile(p, re.IGNORECASE) for p in IGNORE_PATTERNS]

# =============================
# COMPLIANCE CONTEXT DETECTION
# Rejects keyword matches inside routine regulatory filings.
# A text needs 2+ compliance markers to be classified as routine.
# =============================
COMPLIANCE_CONTEXT_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r'pursuant\s+to\s+(regulation|sebi|rule|clause)',
        r'under\s+(regulation|sebi|rule|clause)\s+\d+',
        r'acquisition\s+of\s+shares?\s+(under|pursuant|by\s+way)',
        r'regulation\s+\d+\s*\(',
        r'compliance\s+(with|under|certificate|report)',
        r'disclosure\s+(under|pursuant)',
        r'(inter-?se|inter\s+se)\s+transfer',
        r'(initial|continual)\s+disclosure',
        r'(annual|quarterly)\s+(report|return|submission)',
        r'prescribed\s+under',
        r'in\s+terms\s+of\s+(regulation|clause|rule)',
        r'as\s+per\s+(regulation|sebi|clause)',
        r'intimation\s+under',
        r'outcome\s+of\s+board\s+meeting',
    ]
]

def is_compliance_filing(text):
    """Returns True if the text is a routine regulatory/compliance filing."""
    matches = sum(1 for pat in COMPLIANCE_CONTEXT_PATTERNS if pat.search(text))
    return matches >= 2  # Need 2+ compliance markers to classify as routine

# =============================
# TEXT QUALITY SCORING
# Short/vague filings get penalized, detailed filings pass through.
# =============================
def text_quality_score(text):
    """Score text quality: 0=junk, 1=minimal, 2=decent, 3=rich"""
    word_count = len(text.split())
    if word_count < 8:
        return 0   # Too short to be meaningful (e.g., "acquisition")
    if word_count < 20:
        return 1   # Minimal — single headline
    if word_count < 60:
        return 2   # Decent — some context
    return 3       # Rich — detailed announcement

# =============================
# HISTORICAL PATTERN ANALYSIS
# Checks past wordf signals + fetches price data to validate patterns.
# =============================
def fetch_price_change(symbol, days=3):
    """Fetch price change % over last N days for an NSE/BSE stock using yfinance."""
    try:
        import yfinance as yf
        for suffix in [".NS", ".BO"]:
            try:
                ticker = yf.Ticker(f"{symbol}{suffix}")
                hist = ticker.history(period=f"{days + 5}d")
                if len(hist) >= 2:
                    pct = ((hist['Close'].iloc[-1] - hist['Close'].iloc[0]) / hist['Close'].iloc[0]) * 100
                    return round(pct, 2)
            except Exception:
                continue
    except ImportError:
        pass  # yfinance not installed — skip silently
    except Exception:
        pass
    return None

def analyze_historical_patterns(stock, reasons, ws_history_data):
    """
    PRICE-VALIDATED HISTORICAL PATTERN ANALYSIS

    For each past wordf signal with similar keywords:
    1. Extract the signal date from past entry
    2. Fetch what ACTUALLY happened to the stock price after that signal
    3. Compute win rate (did similar news actually move price correctly?)
    4. Boost confidence only if past similar signals were CORRECT
    5. Penalize if past similar signals were WRONG

    Returns: (boost, analysis_str)
        boost: int (-10 to +20) adjustment to confidence
        analysis_str: human-readable summary of historical validation
    """
    if not ws_history_data:
        return 0, ""

    try:
        import yfinance as yf
    except ImportError:
        return 0, "yfinance not installed"

    # ── Find past signals with matching patterns ────────────────────────
    matching_past_signals = []

    for row in ws_history_data:
        if len(row) < 8:
            continue

        past_time    = str(row[0]).strip()        # e.g. "2026-05-21 10:20"
        past_stock   = str(row[1]).strip().upper()
        past_signal  = str(row[5]).strip().upper() if len(row) > 5 else ""
        past_reasons = str(row[7]).strip().lower() if len(row) > 7 else ""

        # Skip separator/timestamp rows
        if past_stock in ("---", "") or "LAST UPDATED" in past_stock.upper():
            continue
        if not past_signal or past_signal not in ("STRONG BUY", "BUY", "STRONG SELL", "SELL"):
            continue

        # Check if any of current reasons match past reasons
        matched = False
        for reason in reasons:
            clean = reason.split("] ")[-1].strip().lower() if "]" in reason else reason.lower()
            if clean and len(clean) > 3 and clean in past_reasons:
                matched = True
                break

        if matched:
            matching_past_signals.append({
                "date": past_time,
                "stock": past_stock,
                "signal": past_signal,
                "reasons": past_reasons,
            })

    if not matching_past_signals:
        return 0, "no matching historical patterns"

    # ── Fetch price movements AFTER each past signal ────────────────────
    wins = 0
    losses = 0
    total_checked = 0
    price_details = []

    # Check unique stocks from past signals (limit to avoid too many API calls)
    checked_stocks = set()
    for sig in matching_past_signals[-10:]:  # Last 10 matching signals max
        past_stock = sig["stock"]

        # Don't re-check same stock multiple times
        if past_stock in checked_stocks:
            continue
        checked_stocks.add(past_stock)

        # Try to parse the signal date
        try:
            from datetime import timedelta
            sig_date = None
            for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d"]:
                try:
                    sig_date = datetime.strptime(sig["date"], fmt)
                    break
                except ValueError:
                    continue

            if not sig_date:
                continue

            # Fetch price data around the signal date
            start_date = sig_date.strftime("%Y-%m-%d")
            end_date   = (sig_date + timedelta(days=7)).strftime("%Y-%m-%d")

            price_change = None
            for suffix in [".NS", ".BO"]:
                try:
                    ticker = yf.Ticker(f"{past_stock}{suffix}")
                    hist = ticker.history(start=start_date, end=end_date)
                    if len(hist) >= 2:
                        price_change = ((hist['Close'].iloc[-1] - hist['Close'].iloc[0])
                                       / hist['Close'].iloc[0]) * 100
                        price_change = round(price_change, 2)
                        break
                except Exception:
                    continue

            if price_change is None:
                continue

            total_checked += 1
            is_buy_signal = "BUY" in sig["signal"]

            # Check if price moved in the RIGHT direction
            if is_buy_signal and price_change > 1.0:
                wins += 1
                price_details.append(f"{past_stock}: +{price_change}% after BUY (WIN)")
            elif not is_buy_signal and price_change < -1.0:
                wins += 1
                price_details.append(f"{past_stock}: {price_change}% after SELL (WIN)")
            elif is_buy_signal and price_change < -1.0:
                losses += 1
                price_details.append(f"{past_stock}: {price_change}% after BUY (LOSS)")
            elif not is_buy_signal and price_change > 1.0:
                losses += 1
                price_details.append(f"{past_stock}: +{price_change}% after SELL (LOSS)")
            else:
                price_details.append(f"{past_stock}: {price_change:+.1f}% (FLAT)")

        except Exception:
            continue

    # ── Compute boost based on historical win rate ──────────────────────
    boost = 0
    analysis = ""

    if total_checked == 0:
        # No price data available, but pattern was seen before
        pattern_count = len(matching_past_signals)
        if pattern_count >= 3:
            boost = 5
            analysis = f"pattern seen {pattern_count}x before (no price data)"
        elif pattern_count >= 1:
            boost = 2
            analysis = f"pattern seen {pattern_count}x before (no price data)"
    else:
        win_rate = wins / total_checked if total_checked > 0 else 0

        if win_rate >= 0.75 and wins >= 2:
            boost = 20   # Very strong: 75%+ win rate with 2+ wins
            analysis = f"STRONG HIST: {wins}W/{losses}L ({win_rate:.0%} win rate)"
        elif win_rate >= 0.60 and wins >= 1:
            boost = 12   # Good: 60%+ win rate
            analysis = f"GOOD HIST: {wins}W/{losses}L ({win_rate:.0%} win rate)"
        elif win_rate >= 0.50:
            boost = 5    # Decent: 50%+ win rate
            analysis = f"OK HIST: {wins}W/{losses}L ({win_rate:.0%} win rate)"
        elif losses > wins:
            boost = -10  # Penalize: pattern historically FAILED
            analysis = f"WEAK HIST: {wins}W/{losses}L ({win_rate:.0%} — PENALIZED)"
        else:
            boost = 0
            analysis = f"MIXED: {wins}W/{losses}L"

    # Print details for debugging
    if price_details:
        print(f"    [HIST] {stock}: {analysis}")
        for detail in price_details[:3]:
            print(f"           {detail}")

    return boost, analysis

# =============================
# KEYWORD MATCH ENGINE
# Returns (buy_score, sell_score, reasons)
# Now includes IGNORE check + compliance context check
# =============================
def event_score(text):
    # Skip routine disclosures immediately (ignore patterns)
    for pat in _IGNORE_COMPILED:
        if pat.search(text):
            return 0, 0, []

    # Skip compliance filings (regulatory noise with 2+ compliance markers)
    if is_compliance_filing(text):
        return 0, 0, []

    buy_score  = 0
    sell_score = 0
    reasons    = []

    for pat, sc, lbl in _BUY_COMPILED:
        if pat.search(text):
            buy_score += sc
            if sc >= 8:
                tag = "CRITICAL BUY"
            elif sc >= 6:
                tag = "STRONG BUY"
            elif sc >= 3:
                tag = "MED BUY"
            else:
                tag = "LIGHT BUY"
            reasons.append(f"[{tag}] {lbl}")

    for pat, sc, lbl in _SELL_COMPILED:
        if pat.search(text):
            sell_score += sc
            tag = "STRONG SELL" if sc <= -6 else ("MED SELL" if sc <= -3 else "LIGHT SELL")
            reasons.append(f"[{tag}] {lbl}")

    return buy_score, sell_score, reasons

# =============================
# MONEY SCORE
# Amplifies BUY side only — large deal values strengthen positive signals.
# Looks for numbers with Cr/crore/lakh/Rs/INR context.
# Avoids false positives from year numbers and percentages.
# =============================
def money_score(text):
    # Remove year-like 4-digit numbers (1990-2099) and percentage patterns
    cleaned = re.sub(r'\b(19|20)\d{2}\b', '', text)
    cleaned = re.sub(r'\d+\s*%', '', cleaned)

    # Look for currency context: "Rs 500 Cr", "INR 1000 crore", "500 crore"
    currency_match = re.findall(
        r'(?:rs\.?\s*|inr\s*|₹\s*)?(\d[\d,]*)\s*(?:cr(?:ore)?s?|lakh|lac|million|billion|mn|bn)',
        cleaned, re.IGNORECASE
    )

    if currency_match:
        values = [int(n.replace(',', '')) for n in currency_match if n.replace(',', '').isdigit()]
        if values:
            val = max(values)
            if val >= 10000:  return 5
            elif val >= 1000: return 4
            elif val >= 500:  return 3
            elif val >= 100:  return 2
            elif val >= 10:   return 1
            return 0

    return 0

# =============================
# CONFIDENCE CALCULATOR
# Maps raw weighted scores to 0–100% independently for BUY and SELL.
# =============================
def compute_confidence(buy_raw, sell_raw):
    buy_conf = 0
    if buy_raw > 0:
        buy_conf = min(100, int((buy_raw / BUY_100_SCORE) * 100))

    sell_conf = 0
    if sell_raw < 0:
        sell_conf = min(100, int((sell_raw / SELL_100_SCORE) * 100))

    return buy_conf, sell_conf

# =============================
# SIGNAL LABEL
# Requires >80% confidence to show any signal
# =============================
def get_signal_label(conf, direction):
    if conf <= BUY_CONF_THRESHOLD:
        return None
    if direction == "BUY":
        return "STRONG BUY" if conf >= 95 else "BUY"
    if direction == "SELL":
        return "STRONG SELL" if conf >= 95 else "SELL"
    return None

# =============================
# SYMBOL EXTRACTION
# =============================
def extract_symbol(source, row):
    if source == "nse":
        if row and row[0].strip():
            return row[0].strip().upper()
    elif source == "bse":
        if len(row) > 1 and row[1].strip():
            return row[1].strip().upper()
    return None

# =============================
# READ SHEETS
# =============================
def read_sheet(ws, source):
    rows = ws.get_all_values()
    if len(rows) < 2:
        return []
    result = []
    for row in rows[1:]:
        if not row:
            continue
        full_text = " ".join([cell.strip() for cell in row if cell.strip()])
        if not full_text:
            continue
        symbol = extract_symbol(source, row)
        if symbol:
            result.append((source, symbol, full_text))
    return result

# =============================
# DEBUG — show what matched for a symbol
# =============================
def debug_symbol(symbol, all_data):
    print(f"\n── DEBUG: {symbol} ──────────────────────────────")
    for source, sym, text in all_data:
        if sym == symbol:
            b, s, reasons = event_score(text)
            m = money_score(text) if b > 0 else 0
            w = SOURCE_WEIGHT.get(source, 1)
            q = text_quality_score(text)
            c = is_compliance_filing(text)
            print(f"  [{source.upper()}] text: {text[:120]}")
            print(f"         buy={b}, sell={s}, money={m}, weight={w}, quality={q}, compliance={c}")
            print(f"         weighted_buy={(b+m)*w}, weighted_sell={s*w}")
            if reasons:
                for r in reasons:
                    print(f"         ↳ {r}")
            else:
                print(f"         ↳ (no keyword matched)")
    print()

# =============================
# MAIN ENGINE (v2 — Enhanced)
# =============================
def run():
    client = get_client()
    sheet  = client.open_by_key(SHEET_ID)

    all_data = []
    for source in ["nse", "bse"]:
        try:
            ws   = sheet.worksheet(source)
            rows = read_sheet(ws, source)
            print(f"[{source.upper()}] Loaded {len(rows)} rows")
            all_data += rows
        except Exception as e:
            print(f"[{source.upper()}] Skipped: {e}")

    if not all_data:
        print("No data loaded. Check sheet names and permissions.")
        return

    # ── INPUT DEDUPLICATION ──────────────────────────────────────────────
    # Same company + similar text from different sources shouldn't double-count
    seen_inputs = set()
    deduped_data = []
    for source, symbol, text in all_data:
        key = (symbol, text[:80].lower().strip())
        if key not in seen_inputs:
            seen_inputs.add(key)
            deduped_data.append((source, symbol, text))
    dup_count = len(all_data) - len(deduped_data)
    all_data = deduped_data
    print(f"Dedup: removed {dup_count} duplicates → {len(all_data)} unique entries")

    # ── LOAD HISTORICAL DATA FOR PATTERN ANALYSIS ────────────────────────
    ws_history_data = []
    try:
        ws_hist = sheet.worksheet("wordf")
        ws_history_data = ws_hist.get_all_values()[1:]  # Skip header
        print(f"Historical: loaded {len(ws_history_data)} past signal rows")
    except Exception:
        print("Historical: no past data found (first run)")

    # ── SCORE AGGREGATION ────────────────────────────────────────────────
    stock_scores = {}
    skipped_quality = 0
    skipped_compliance = 0
    skipped_bse_short = 0
    skipped_no_match = 0

    for source, symbol, text in all_data:
        # ─ TEXT QUALITY CHECK ─
        quality = text_quality_score(text)
        if quality == 0:
            skipped_quality += 1
            continue

        # ─ EVENT SCORE (includes ignore + compliance checks) ─
        b, s, reasons = event_score(text)

        # Check if it was filtered by compliance/ignore (returns 0,0,[])
        if b == 0 and s == 0 and not reasons:
            # Could be no-match or filtered — check if compliance
            if is_compliance_filing(text):
                skipped_compliance += 1
                continue
            elif any(pat.search(text) for pat in _IGNORE_COMPILED):
                skipped_no_match += 1
                continue
            # No keywords matched at all
            skipped_no_match += 1
            continue

        # ─ BSE SHORT-TEXT HANDLER ─
        # BSE headlines are often just 4-6 words — require stronger evidence
        if source == "bse" and quality <= 1:
            if b < 6 and s > -6:  # Not a strong signal
                skipped_bse_short += 1
                continue

        m = money_score(text) if b > 0 else 0
        weight  = SOURCE_WEIGHT.get(source, 1)
        b_total = (b + m) * weight
        s_total = s * weight

        if symbol not in stock_scores:
            stock_scores[symbol] = {
                "buy_score":  0,
                "sell_score": 0,
                "reasons":    [],
                "sources":    set(),
                "texts":      []
            }

        stock_scores[symbol]["buy_score"]  += b_total
        stock_scores[symbol]["sell_score"] += s_total
        stock_scores[symbol]["reasons"].extend(reasons)
        stock_scores[symbol]["sources"].add(source.upper())
        stock_scores[symbol]["texts"].append(text[:100])

    print(f"\nFiltered: {skipped_quality} quality | {skipped_compliance} compliance | "
          f"{skipped_bse_short} BSE-short | {skipped_no_match} no-match")
    print(f"Stocks with signals: {len(stock_scores)}")

    # ── HISTORICAL PATTERN BOOST (price-validated) ─────────────────────────
    hist_boosts = 0
    hist_penalties = 0
    for stock, data in stock_scores.items():
        hist_boost, hist_analysis = analyze_historical_patterns(stock, data["reasons"], ws_history_data)
        if hist_boost > 0:
            data["buy_score"] += hist_boost
            data["reasons"].append(f"[HIST +{hist_boost}] {hist_analysis}")
            hist_boosts += 1
        elif hist_boost < 0:
            data["buy_score"] += hist_boost  # Penalize
            data["sell_score"] += hist_boost
            data["reasons"].append(f"[HIST {hist_boost}] {hist_analysis}")
            hist_penalties += 1
    if hist_boosts or hist_penalties:
        print(f"Historical: {hist_boosts} boosted, {hist_penalties} penalized")

    # ── MARKET INTELLIGENCE ENRICHMENT ───────────────────────────────────
    enriched_count = 0
    freshness_skipped = 0
    if MARKET_UTILS_OK:
        print("\nEnriching signals with market intelligence...")
        for stock, data in stock_scores.items():
            # Check freshness (already priced in?)
            is_stale, today_change = check_freshness(stock)
            if is_stale:
                # If stock already moved 3%+ in signal direction, suppress
                if data["buy_score"] > 0 and today_change > 3.0:
                    data["buy_score"] = 0
                    data["reasons"].append(f"[STALE] already +{today_change:.1f}% today")
                    freshness_skipped += 1
                    continue
                elif data["sell_score"] < 0 and today_change < -3.0:
                    data["sell_score"] = 0
                    data["reasons"].append(f"[STALE] already {today_change:.1f}% today")
                    freshness_skipped += 1
                    continue

            # Get direction for enrichment
            direction = "BUY" if data["buy_score"] > abs(data["sell_score"]) else "SELL"

            # Full enrichment
            enrichment = enrich_signal(
                stock, direction, data["reasons"],
                deal_value_cr=0, sheet_history=ws_history_data
            )

            # Apply adjustments
            adj = enrichment["total_adjustment"]
            tw = enrichment["time_weight"]

            if adj != 0:
                if direction == "BUY":
                    data["buy_score"] = int(data["buy_score"] + adj)
                else:
                    data["sell_score"] = int(data["sell_score"] - abs(adj))

            # Apply time weight
            if tw < 1.0:
                data["buy_score"] = int(data["buy_score"] * tw)
                data["sell_score"] = int(data["sell_score"] * tw)

            # Add enrichment details to reasons
            for detail in enrichment["details"]:
                data["reasons"].append(f"[MKT] {detail}")

            enriched_count += 1

        print(f"Enriched: {enriched_count} stocks | Freshness-skipped: {freshness_skipped}")

    # ── EVALUATE SIGNALS ─────────────────────────────────────────────────
    buy_output  = []
    sell_output = []

    W = 80
    print(f"\n{'='*W}")
    print(f"  ULTRA-RELIABLE SIGNALS  |  Threshold: >{BUY_CONF_THRESHOLD}%  |  v2 Engine")
    print(f"{'='*W}")
    print(f"{'STOCK':<20} {'BUY_RAW':>8} {'SELL_RAW':>9} {'BUY%':>6} {'SELL%':>6}  SIGNAL")
    print(f"{'-'*W}")

    for stock, data in sorted(stock_scores.items()):
        buy_raw  = data["buy_score"]
        sell_raw = data["sell_score"]
        reasons  = list(dict.fromkeys(data["reasons"]))
        sources  = ", ".join(sorted(data["sources"]))

        buy_conf, sell_conf = compute_confidence(buy_raw, sell_raw)

        has_buy  = buy_conf  > BUY_CONF_THRESHOLD
        has_sell = sell_conf > SELL_CONF_THRESHOLD

        # Mixed signal guard — conflicting high-confidence signals → suppress
        if has_buy and has_sell:
            print(f"{stock:<20} {buy_raw:>8} {sell_raw:>9} {buy_conf:>5}% {sell_conf:>5}%  ⚠️  MIXED — SUPPRESSED")
            continue

        now_str = datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%Y-%m-%d %H:%M")

        if has_buy:
            signal = get_signal_label(buy_conf, "BUY")
            if signal:
                buy_reasons = [r for r in reasons if "BUY" in r or "HIST" in r or "CRITICAL" in r]
                reason_str  = " | ".join(buy_reasons[:5])
                print(f"{stock:<20} {buy_raw:>8} {sell_raw:>9} {buy_conf:>5}% {'—':>5}   {signal}  [{sources}]")
                buy_output.append([now_str, stock, buy_raw, sell_raw, buy_conf, signal, sources, reason_str])

        elif has_sell:
            signal = get_signal_label(sell_conf, "SELL")
            if signal:
                sell_reasons = [r for r in reasons if "SELL" in r]
                reason_str   = " | ".join(sell_reasons[:5])
                print(f"{stock:<20} {buy_raw:>8} {sell_raw:>9} {'—':>5}  {sell_conf:>5}%  {signal}  [{sources}]")
                sell_output.append([now_str, stock, buy_raw, sell_raw, sell_conf, signal, sources, reason_str])

    # ── OUTPUT DEDUPLICATION ─────────────────────────────────────────────
    # Prevent same stock appearing multiple times with same signal
    seen_output = set()
    deduped_buy = []
    for row in buy_output:
        sig = (row[1], row[5])  # (stock, signal)
        if sig not in seen_output:
            seen_output.add(sig)
            deduped_buy.append(row)
    deduped_sell = []
    for row in sell_output:
        sig = (row[1], row[5])
        if sig not in seen_output:
            seen_output.add(sig)
            deduped_sell.append(row)
    buy_output = deduped_buy
    sell_output = deduped_sell

    total = len(buy_output) + len(sell_output)
    print(f"\n  BUY Signals: {len(buy_output)}  |  SELL Signals: {len(sell_output)}  |  Total: {total}")
    print(f"{'='*W}\n")

    if total == 0:
        print(f"No signals crossed the {BUY_CONF_THRESHOLD}% confidence threshold.")
        print("This is EXPECTED — the v2 engine is strict by design.")
        print("Only genuinely price-moving events should appear here.\n")

    # ── PRICE ENRICHMENT (yfinance) ──────────────────────────────────────
    # Fetch current price trend for each signal to add context
    all_output = buy_output + sell_output
    price_enriched = 0
    for row in all_output:
        stock_sym = row[1]
        price_chg = fetch_price_change(stock_sym, days=3)
        if price_chg is not None:
            row.append(f"{price_chg:+.1f}%")
            price_enriched += 1
        else:
            row.append("N/A")

    if price_enriched:
        print(f"Price data fetched for {price_enriched}/{len(all_output)} stocks")

    # ── WRITE TO GOOGLE SHEET ────────────────────────────────────────────
    try:
        ws_out = sheet.worksheet("wordf")
    except Exception:
        ws_out = sheet.add_worksheet(title="wordf", rows="2000", cols="12")

    existing = ws_out.get_all_values()
    if not existing:
        ws_out.append_row([
            "Time", "Stock", "Buy Raw", "Sell Raw",
            "Confidence (%)", "Signal", "Sources", "Matched Reasons", "3D Price Δ"
        ])

    all_output.sort(key=lambda x: x[4], reverse=True)

    if all_output:
        ws_out.append_rows(all_output)

    ws_out.append_row(["---", "Last Updated (IST):", get_ist_time(), "", "", "", "", "", ""])
    print(f"Results written to 'wordf' sheet.")

    # ── TELEGRAM ALERTS ──────────────────────────────────────────────────
    alerts_sent = 0
    if ALERTS_OK and all_output:
        for row in all_output:
            stock_sym = row[1]
            signal_lbl = row[5]
            conf = row[4]
            reason_txt = row[7] if len(row) > 7 else ""
            if send_alert(stock_sym, signal_lbl, conf, reason_txt, source="wordf v3"):
                alerts_sent += 1
        if alerts_sent:
            top_picks = [row[1] for row in all_output[:5]]
            send_summary(len(buy_output), len(sell_output), top_picks, source="wordf v3")

    # ── SUMMARY ──────────────────────────────────────────────────────────
    print(f"\n{'='*W}")
    print(f"  WORDF v3 SUMMARY")
    print(f"  Input: {len(deduped_data)} unique entries")
    print(f"  Filtered: {skipped_quality + skipped_compliance + skipped_bse_short + skipped_no_match} total")
    print(f"  Signals: {total} (BUY: {len(buy_output)}, SELL: {len(sell_output)})")
    if hist_boosts:
        print(f"  Historical boosts: {hist_boosts}")
    if price_enriched:
        print(f"  Price enriched: {price_enriched}")
    if MARKET_UTILS_OK:
        print(f"  Market enriched: {enriched_count} | Freshness-skipped: {freshness_skipped}")
    if alerts_sent:
        print(f"  Telegram alerts: {alerts_sent}")
    print(f"{'='*W}")

# =============================
# ENTRY POINT
# =============================
if __name__ == "__main__":
    run()
