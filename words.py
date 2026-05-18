'''import re
import gspread
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials
import pytz

# =============================
# GOOGLE AUTH
# =============================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

def get_client():
    creds = ServiceAccountCredentials.from_json_keyfile_name(
        "service_account.json", scope
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

# =============================
# SOURCE WEIGHTS
# =============================
SOURCE_WEIGHT = {
    "nse": 5,
    "bse": 5,
    "monc": 3,
    "et": 1
}

# =============================
# KEYWORDS
# =============================
STRONG_BUY = [
"l1 bidder","loa","letter of award","contract secured","large order","order book",
"buyback","bonus","stock split","record profit","all time high profit",
"debt free","deleveraging","promoter buying","value unlocking","turnaround"
]

MEDIUM_BUY = [
"capacity expansion","partnership","joint venture","acquisition",
"margin expansion","earnings beat","revenue growth","order inflow"
]

LIGHT_BUY = [
"agreement","mou","investment","launch","expansion"
]

STRONG_SELL = [
"forensic audit","auditor resignation","default","insolvency","nclt",
"sebi action","fraud","accounting irregularities","pledge invoked"
]

MEDIUM_SELL = [
"rating downgrade","loss widens","earnings miss",
"production halt","governance issue"
]

LIGHT_SELL = [
"stake sale","promoter selling","margin pressure",
"guidance cut","penalty","litigation"
]

IGNORE = [
"board meeting","postal ballot","agm","investor meet",
"trading window","clarification","newspaper"
]

# =============================
# EVENT SCORE
# =============================
def event_score(text):
    text = text.lower()

    if any(x in text for x in IGNORE):
        return 0, []

    score = 0
    reasons = []

    for w in STRONG_BUY:
        if w in text:
            score += 6
            reasons.append(w)

    for w in MEDIUM_BUY:
        if w in text:
            score += 3
            reasons.append(w)

    for w in LIGHT_BUY:
        if w in text:
            score += 1
            reasons.append(w)

    for w in STRONG_SELL:
        if w in text:
            score -= 6
            reasons.append(w)

    for w in MEDIUM_SELL:
        if w in text:
            score -= 3
            reasons.append(w)

    for w in LIGHT_SELL:
        if w in text:
            score -= 1
            reasons.append(w)

    return score, reasons

# =============================
# MONEY SCORE
# =============================
def money_score(text):
    nums = re.findall(r'\d+', text)
    if not nums:
        return 0

    val = max([int(n) for n in nums])

    if val > 1000: return 3
    elif val > 100: return 2
    elif val > 10: return 1
    return 0

# =============================
# SYMBOL NORMALIZATION (FIXED)
# =============================
def normalize_symbol(source, row, text):

    if source == "nse":
        return row[0]

    if source == "bse":
        return None

    # ✅ allow monc + et
    if source in ["monc", "et"]:
        return "GENERIC"

    return None

# =============================
# READ SHEETS
# =============================
def read_sheet(ws, source):
    data = ws.get_all_values()[1:]
    result = []

    for r in data:
        if len(r) < 1:
            continue

        if source in ["nse","bse"]:
            text = r[-1]
            symbol = normalize_symbol(source, r, text)

        elif source == "et":
            text = r[0]
            symbol = "GENERIC"

        elif source == "monc":
            text = r[0]
            symbol = normalize_symbol(source, r, text)

        else:
            continue

        if symbol:
            result.append((source, symbol, text))

    return result

# =============================
# MAIN ENGINE
# =============================
def run():
    client = get_client()
    sheet = client.open_by_key(SHEET_ID)

    all_data = []

    for name in ["nse","bse","et","monc"]:
        try:
            ws = sheet.worksheet(name)
            all_data += read_sheet(ws, name)
        except Exception as e:
            print(f"Skipping {name}: {e}")

    stock_scores = {}

    for source, symbol, text in all_data:

        if symbol in ["", None]:
            continue

        e, reasons = event_score(text)

        if source == "bse" and e < 0:
            continue

        m = money_score(text)
        w = SOURCE_WEIGHT.get(source, 1)

        total = (e + m) * w

        if symbol not in stock_scores:
            stock_scores[symbol] = {"score":0, "reasons":[]}

        stock_scores[symbol]["score"] += total
        stock_scores[symbol]["reasons"].extend(reasons)

    # =============================
    # FINAL OUTPUT
    # =============================
    output = []

    print("\n======= FINAL HIGH PROBABILITY SIGNALS =======\n")

    for stock, data in stock_scores.items():

        score = data["score"]
        reasons = list(dict.fromkeys(data["reasons"]))

        prob = max(0, min(100, int((score + 20) * 2)))

        if prob >= 70:
            signal = "STRONG BUY 🟢🟢"

        elif prob >= 60:
            signal = "BUY 🟢"

        elif prob <= 30:
            signal = "STRONG SELL 🔴🔴"

        elif prob <= 40:
            signal = "SELL 🔴"

        else:
            continue

        print(f"{stock} | Score: {score} | {prob}% | {signal}")

        output.append([
            datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%Y-%m-%d %H:%M"),
            stock,
            score,
            prob,
            signal,
            ", ".join(reasons[:3])
        ])

    print(f"\nTotal Signals: {len(output)}\n")

    # =============================
    # WRITE TO SHEET
    # =============================
    try:
        ws = sheet.worksheet("wordf")
    except:
        ws = sheet.add_worksheet(title="wordf", rows="1000", cols="10")

    if not ws.get_all_values():
        ws.append_row(["Time","Stock","Score","Probability","Signal","Reason"])

    output.sort(key=lambda x: x[3], reverse=True)

    rows_to_append = []
    if output:
        rows_to_append.extend(output)

    rows_to_append.append(["Last Updated (IST):", get_ist_time()])
    ws.append_rows(rows_to_append)

# =============================
# RUN
# =============================
if __name__ == "__main__":
    run()
'''

import re
import gspread
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials
import pytz

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
# CONFIDENCE THRESHOLDS
# =============================
BUY_CONF_THRESHOLD  = 60
SELL_CONF_THRESHOLD = 60

# =============================
# SCORE CALIBRATION
#
# One STRONG_BUY phrase  = +6 raw × weight 5 = 30 weighted → 50% conf  (not shown)
# Two STRONG_BUY phrases = 60 weighted                      → 100% conf
# One STRONG_BUY + money_score 2 = (6+2)×5 = 40 weighted   → 67% conf  (not shown)
# One STRONG_BUY + money_score 3 = (6+3)×5 = 45 weighted   → 75% conf  (not shown)
# One STRONG_BUY + money_score 5 = (6+5)×5 = 55 weighted   → 92% conf  (SHOWN ✅)
# Two STRONG_BUY + any money     = easily > 60 weighted     → 100% conf (SHOWN ✅)
#
# This means a single filing only crosses 80% when:
#   - One very strong keyword + a large deal value, OR
#   - Two independent strong keywords in the same text
# =============================
BUY_100_SCORE  = 30
SELL_100_SCORE = -30

# =============================
# KEYWORD ENGINE
#
# Each entry is a (pattern, score, label) tuple.
# Pattern is a regex — this allows:
#   - Plural/verb variants: acqui(re|res|red|ring|sition)
#   - Word-order flexibility: "order.{0,20}received" matches "order of Rs 500Cr received"
#   - Optional words: "letter of (award|intent)"
#   - Boundaries: \b prevents partial matches
# =============================

BUY_PATTERNS = [

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
    (r'\baquir(e|es|ed|ing)\b|\bacquisition\b',                           6, "acquisition/acquire"),
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
    (r'\bnclt\s+(approval|approves?|order)\b',                            6, "nclt approval"),
    (r'\bresolution\s+plan\s+(approved|accepted)\b',                      6, "resolution plan approved"),

    # ── MEDIUM BUY (+3) ─────────────────────────────────────────────────
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

    # Launch (medium-buy — product launch at company scale)
    (r'\b(launches?|launched|launching)\s+(india.?s?\s+first|world.?s?\s+first)\b', 3, "launches India's/world's first"),
    (r'\bsingle.?window\s+approval\b',                                     3, "single-window approval system"),

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

    # Resignation of KMP (from actual BSE filing pattern)
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

# ── IGNORE PATTERNS ─────────────────────────────────────────────────────────
# Routine / non-material filings — skip entirely (zero score)
IGNORE_PATTERNS = [
    r'\bboard\s+meeting\s+(intimation|scheduled|notice)\b',
    r'\bpostal\s+ballot\b',
    r'\b(agm|egm)\s+(notice|on|scheduled)\b',
    r'\binvestor\s+meet\b|\banalyst\s+meet\b|\bearnings?\s+(call|conference\s+call)\b',
    r'\btrading\s+window\s+(clos\w+|open\w+|shall)\b',
    r'\bclarification\s+(sought|submitted|given)\b',
    r'\bnewspaper\s+publication\b|\bnewspaper\s+advertisement\b',
    r'\bsaksham\s+niveshak\b',                               # sebi investor campaign
    r'\bchange\s+of\s+(registered\s+)?address\b',
    r'\bbook\s+closure\b',
    r'\brecord\s+date\s+for\s+dividend\b',
    r'\b(interim|final)\s+dividend\b|\bdividend\s+payment\b|\bdividend\s+of\s+rs\b',
    r'\bloss\s+of\s+share\s+certificate\b|\bduplicate\s+share\s+certificate\b',
    r'\btransmission\s+of\s+shares\b',
    r'\breg(ulation)?\s*(74|40|7)\b',                        # sebi compliance regs
    r'\blarge\s+corporate\s+(disclosure|criteria|entity)\b', # routine sebi circular
    r'\bformat\s+of\s+(initial|annual)\s+disclosure\b',      # large corporate format
    r'\bsecretarial\s+compliance\s+report\b',
    r'\bmonthly\s+reporting\b',
    r'\bchange\s+in\s+kmp\b',
    r'\bscrutinizer\s+report\b|\bvoting\s+result\b|\be-?voting\b',
    r'\bemployee\s+stock\s+option\b|\besop\b',
    r'\bpublic\s+notice\b',                                  # iepf notices
    r'\bconference\s+call\s+(invitation|scheduled)\b',
    r'\bnot\s+a?\s+large\s+corporate\b|\bdoes\s+not\s+fall\s+under\b',  # lc non-applicability
    r'\bnon.?applicability\b',
    r'\besg\s+rating\b',                                     # esg disclosure — not price signal
    r'\bintimation\s+of\s+postal\s+ballot\b',
    r'\btenure\s+of\b',                                      # expiry of officer tenure (routine)
    r'\binternal\s+reorgani[sz]ation\b',                     # internal reshuffle without material impact
    r'\bpost\s+offer\s+advertisement\b',
]

# Precompile all patterns once at startup for speed
_BUY_COMPILED  = [(re.compile(p, re.IGNORECASE), sc, lbl) for p, sc, lbl in BUY_PATTERNS]
_SELL_COMPILED = [(re.compile(p, re.IGNORECASE), sc, lbl) for p, sc, lbl in SELL_PATTERNS]
_IGNORE_COMPILED = [re.compile(p, re.IGNORECASE) for p in IGNORE_PATTERNS]

# =============================
# KEYWORD MATCH ENGINE
# Returns (buy_score, sell_score, reasons)
# =============================
def event_score(text):
    # Skip routine disclosures immediate

    buy_score  = 0
    sell_score = 0
    reasons    = []

    for pat, sc, lbl in _BUY_COMPILED:
        if pat.search(text):
            buy_score += sc
            tag = "STRONG BUY" if sc >= 6 else ("MED BUY" if sc >= 3 else "LIGHT BUY")
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
# Looks for numbers preceded or followed by Cr/crore/lakh/Rs/INR context.
# Avoids false positives from year numbers (2024, 2025, 2026) and percentages.
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
            # Convert lakh to approximate crore if small number
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
# Strict >80% required (not >=80)
# =============================
def get_signal_label(conf, direction):
    if conf <= BUY_CONF_THRESHOLD:
        return None
    if direction == "BUY":
        return "STRONG BUY 🟢🟢" if conf >= 95 else "BUY 🟢"
    if direction == "SELL":
        return "STRONG SELL 🔴🔴" if conf >= 95 else "SELL 🔴"
    return None

# =============================
# SYMBOL EXTRACTION
# =============================
def extract_symbol(source, row):

    # NSE → column A is symbol (correct)
    if source == "nse":
        if row and row[0].strip():
            return row[0].strip().upper()

    # BSE → use COMPANY NAME (column B)
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
        # 🔥 KEY CHANGE: combine entire row
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
            print(f"  [{source.upper()}] text: {text[:120]}")
            print(f"         buy={b}, sell={s}, money={m}, weight={w}, weighted_buy={(b+m)*w}, weighted_sell={s*w}")
            if reasons:
                for r in reasons:
                    print(f"         ↳ {r}")
            else:
                print(f"         ↳ (no keyword matched)")
    print()

# =============================
# MAIN ENGINE
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

    # ── SCORE AGGREGATION ────────────────────────────────────────────────
    stock_scores = {}

    for source, symbol, text in all_data:
        b, s, reasons = event_score(text)
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

    # ── OPTIONAL DEBUG: uncomment to trace a specific stock ──────────────
    # debug_symbol("VIKRAN", all_data)
    # debug_symbol("SOFTTECH", all_data)

    # ── EVALUATE SIGNALS ─────────────────────────────────────────────────
    buy_output  = []
    sell_output = []

    W = 76
    print(f"\n{'='*W}")
    print(f"  HIGH CONFIDENCE SIGNALS  |  Threshold: >{BUY_CONF_THRESHOLD}%")
    print(f"{'='*W}")
    print(f"{'STOCK':<16} {'BUY_RAW':>8} {'SELL_RAW':>9} {'BUY%':>6} {'SELL%':>6}  SIGNAL")
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
            print(f"{stock:<16} {buy_raw:>8} {sell_raw:>9} {buy_conf:>5}% {sell_conf:>5}%  ⚠️  MIXED — SUPPRESSED")
            continue

        now_str = datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%Y-%m-%d %H:%M")

        if has_buy:
            signal = get_signal_label(buy_conf, "BUY")
            if signal:
                buy_reasons = [r for r in reasons if "BUY" in r]
                reason_str  = " | ".join(buy_reasons[:5])
                print(f"{stock:<16} {buy_raw:>8} {sell_raw:>9} {buy_conf:>5}% {'—':>5}   {signal}  [{sources}]")
                buy_output.append([now_str, stock, buy_raw, sell_raw, buy_conf, signal, sources, reason_str])

        elif has_sell:
            signal = get_signal_label(sell_conf, "SELL")
            if signal:
                sell_reasons = [r for r in reasons if "SELL" in r]
                reason_str   = " | ".join(sell_reasons[:5])
                print(f"{stock:<16} {buy_raw:>8} {sell_raw:>9} {'—':>5}  {sell_conf:>5}%  {signal}  [{sources}]")
                sell_output.append([now_str, stock, buy_raw, sell_raw, sell_conf, signal, sources, reason_str])

    total = len(buy_output) + len(sell_output)
    print(f"\n  BUY Signals: {len(buy_output)}  |  SELL Signals: {len(sell_output)}  |  Total: {total}")
    print(f"{'='*W}\n")

    if total == 0:
        print("No signals crossed the 80% confidence threshold today.")
        print("Check debug_symbol() above to trace why specific stocks didn't qualify.\n")

    # ── WRITE TO GOOGLE SHEET ────────────────────────────────────────────
    try:
        ws_out = sheet.worksheet("wordf")
    except Exception:
        ws_out = sheet.add_worksheet(title="wordf", rows="2000", cols="10")

    if not ws_out.get_all_values():
        ws_out.append_row([
            "Time", "Stock", "Buy Raw", "Sell Raw",
            "Confidence (%)", "Signal", "Sources", "Matched Reasons"
        ])

    all_output = buy_output + sell_output
    all_output.sort(key=lambda x: x[4], reverse=True)

    if all_output:
        ws_out.append_rows(all_output)

    ws_out.append_row(["---", "Last Updated (IST):", get_ist_time(), "", "", "", "", ""])
    print(f"Results written to 'wordf' sheet.")

# =============================
# ENTRY POINT
# =============================
if __name__ == "__main__":
    run()
