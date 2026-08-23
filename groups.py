"""
Investment banking group taxonomy.

Defines the six coverage (industry) groups and six product groups that
structure the newsletter, plus the keyword sets used to route headlines to
each group. Mirrors how a bulge-bracket bank is actually organized, so the
reader can map each story to the desk that would own it.

Each group carries:
  title           display name in the email
  subtitle        what the desk covers, rendered under the title
  kind            "coverage" | "product" | "standing"
  accent          hex color for the group chip and rule
  short           2-4 char label for the navigation grid
  desk_note       one line on what this desk actually does
  keywords        routing terms, lowercase, matched against headline + summary
  tickers         bellwether names whose moves anchor the group's context
  focus           editorial mandate handed to the model for this group
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Coverage groups (industry)
# ---------------------------------------------------------------------------

COVERAGE_GROUPS: dict[str, dict] = {
    "tmt": {
        "title": "TMT",
        "subtitle": "Technology, Media & Telecom",
        "kind": "coverage",
        "accent": "#2563EB",
        "short": "TMT",
        "desk_note": (
            "Software, semis, internet, media and telecom. The most active "
            "coverage group by deal count and the one most exposed to rates."
        ),
        "keywords": [
            "software", "saas", "semiconductor", "chip", "chipmaker", "foundry",
            "artificial intelligence", " ai ", "machine learning", "data center",
            "cloud", "hyperscaler", "nvidia", "amd", "intel", "tsmc", "asml",
            "broadcom", "qualcomm", "micron", "arm holdings", "microsoft",
            "alphabet", "google", "meta platforms", "apple", "amazon", "oracle",
            "salesforce", "adobe", "servicenow", "palantir", "snowflake",
            "datadog", "crowdstrike", "cybersecurity", "streaming", "netflix",
            "disney", "warner bros", "paramount", "comcast", "charter",
            "verizon", "at&t", "t-mobile", "telecom", "spectrum auction",
            "media company", "advertising", "gaming", "video game",
        ],
        "tickers": ["NVDA", "MSFT", "GOOGL", "META", "AVGO", "AMD", "ORCL", "NFLX"],
        "focus": (
            "Software, semiconductors, internet, media and telecom. Prioritize "
            "AI infrastructure capex, competitive share shifts, semiconductor "
            "cycle signals, and any move that reprices the group's multiples. "
            "TMT trades on growth durability and cost of capital, so always "
            "connect the story to rates or to the AI capex cycle."
        ),
    },
    "healthcare": {
        "title": "Healthcare",
        "subtitle": "Pharma, Biotech, Devices & Services",
        "kind": "coverage",
        "accent": "#0D9488",
        "short": "HC",
        "desk_note": (
            "Pharma, biotech, medtech and providers. Binary catalysts (FDA, "
            "trial data) and the patent cliff drive the equities and the M&A."
        ),
        "keywords": [
            "fda", "pdufa", "clinical trial", "phase 3", "phase iii", "phase 2",
            "drug approval", "complete response letter", "biotech", "biotechnology",
            "pharmaceutical", "pharma", "oncology", "obesity drug", "glp-1",
            "eli lilly", "novo nordisk", "pfizer", "merck", "abbvie", "amgen",
            "bristol myers", "gilead", "regeneron", "vertex", "moderna",
            "johnson & johnson", "unitedhealth", "cigna", "humana", "cvs health",
            "elevance", "hospital", "medicare", "medicaid", "cms", "medical device",
            "medtronic", "abbott", "stryker", "boston scientific",
            "intuitive surgical", "patent cliff", "biosimilar", "drug pricing",
            "pbm", "clinical data", "vaccine", "therapy", "rare disease",
        ],
        "tickers": ["LLY", "UNH", "JNJ", "MRK", "ABBV", "PFE", "TMO", "ABT"],
        "focus": (
            "FDA decisions, pivotal trial data, drug pricing policy, managed "
            "care margins, and pipeline M&A. Quantify addressable population, "
            "peak sales potential, or the earnings hole a patent cliff creates. "
            "Healthcare is the classic defensive group, so also read what its "
            "relative performance says about risk appetite."
        ),
    },
    "industrials": {
        "title": "Industrials",
        "subtitle": "Aerospace, Defense, Machinery & Transport",
        "kind": "coverage",
        "accent": "#7C3AED",
        "short": "IND",
        "desk_note": (
            "Aerospace and defense, machinery, transport and building products. "
            "The most direct read on the real economy and the capex cycle."
        ),
        "keywords": [
            "manufacturing", "factory", "industrial production", "pmi",
            "purchasing managers", "ism", "aerospace", "boeing", "airbus",
            "defense contract", "pentagon", "lockheed", "rtx", "raytheon",
            "northrop", "general dynamics", "l3harris", "caterpillar", "deere",
            "honeywell", "ge aerospace", "emerson", "eaton", "parker hannifin",
            "rockwell", "3m", "illinois tool", "machinery", "heavy equipment",
            "railroad", "union pacific", "csx", "norfolk southern", "freight",
            "trucking", "ups", "fedex", "logistics", "supply chain",
            "infrastructure spending", "construction", "building products",
            "airline", "delta air", "united airlines", "backlog", "book-to-bill",
        ],
        "tickers": ["CAT", "GE", "RTX", "HON", "UNP", "DE", "LMT", "ETN"],
        "focus": (
            "PMI and ISM prints, defense awards, aerospace deliveries, freight "
            "rates, backlogs and book-to-bill. Industrials are early-cycle, so "
            "read every story as a signal about where the capex and inventory "
            "cycle sits. Quantify backlog, order growth or contract value."
        ),
    },
    "consumer": {
        "title": "Consumer & Retail",
        "subtitle": "Retail, Restaurants, Staples & Leisure",
        "kind": "coverage",
        "accent": "#DB2777",
        "short": "CNS",
        "desk_note": (
            "Retail, restaurants, staples, apparel and leisure. Roughly 70% of "
            "GDP runs through this desk, the cleanest read on the US household."
        ),
        "keywords": [
            "retail sales", "same-store sales", "comparable sales", "comp sales",
            "walmart", "target", "costco", "home depot", "lowe", "amazon retail",
            "dollar general", "dollar tree", "tjx", "ross stores", "kroger",
            "procter & gamble", "coca-cola", "pepsico", "mondelez", "general mills",
            "kraft heinz", "colgate", "kimberly-clark", "unilever", "nestle",
            "nike", "lululemon", "under armour", "gap inc", "restaurant",
            "mcdonald", "starbucks", "chipotle", "yum brands", "darden",
            "consumer confidence", "consumer sentiment", "consumer spending",
            "discretionary spending", "trade-down", "promotional environment",
            "airbnb", "booking holdings", "marriott", "hilton", "cruise line",
            "carnival", "royal caribbean", "casino", "las vegas sands",
            "e-commerce", "holiday shopping", "back-to-school", "tariff on goods",
        ],
        "tickers": ["WMT", "COST", "HD", "PG", "KO", "MCD", "NKE", "SBUX"],
        "focus": (
            "Comparable sales, traffic versus ticket, gross margin and the "
            "promotional environment, plus what management says about the "
            "low-income consumer. Distinguish volume from price. Consumer is "
            "the cleanest read on household health, so tie the story back to "
            "real income, credit and the labor market."
        ),
    },
    "energy_power": {
        "title": "Energy & Power",
        "subtitle": "Oil & Gas, Utilities, Renewables",
        "kind": "coverage",
        "accent": "#B45309",
        "short": "NRG",
        "desk_note": (
            "Upstream, midstream, refining, utilities and renewables. Power "
            "demand from data centers has turned this into a growth desk."
        ),
        "keywords": [
            "crude oil", "wti", "brent", "opec", "opec+", "oil production",
            "natural gas", "lng", "henry hub", "refinery", "refining margin",
            "crack spread", "exxon", "chevron", "conocophillips", "occidental",
            "eog resources", "devon energy", "diamondback", "pioneer natural",
            "schlumberger", "halliburton", "baker hughes", "pipeline",
            "midstream", "williams companies", "energy transfer", "kinder morgan",
            "utility", "utilities", "nextera", "duke energy", "southern company",
            "dominion energy", "constellation energy", "vistra", "power purchase",
            "electricity demand", "grid", "nuclear", "smr", "small modular",
            "renewable", "solar", "first solar", "wind energy", "offshore wind",
            "battery storage", "lithium", "shale", "rig count", "drilling",
            "strategic petroleum reserve", "energy transition", "carbon capture",
        ],
        "tickers": ["XOM", "CVX", "COP", "NEE", "SLB", "OXY", "VST", "CEG"],
        "focus": (
            "Crude and gas price drivers, OPEC+ policy, refining margins, rig "
            "counts, and the power demand story from data centers. Treat "
            "commodity moves as macro signals as well as sector news: connect "
            "them to inflation, to input costs, and to the energy equity trade."
        ),
    },
    "fig": {
        "title": "FIG",
        "subtitle": "Financial Institutions Group",
        "kind": "coverage",
        "accent": "#0E7490",
        "short": "FIG",
        "desk_note": (
            "Banks, insurers, asset managers, exchanges and fintech. The group "
            "whose earnings are a direct function of the rate curve above."
        ),
        "keywords": [
            "bank", "banks", "banking", "jpmorgan", "goldman sachs",
            "morgan stanley", "bank of america", "citigroup", "wells fargo",
            "regional bank", "pnc financial", "us bancorp", "truist", "schwab",
            "net interest income", "net interest margin", "nim", "deposit",
            "deposit beta", "loan growth", "credit loss", "charge-off",
            "delinquency", "loan loss provision", "basel", "capital requirement",
            "stress test", "ccar", "cet1", "insurance", "insurer", "reinsurance",
            "berkshire hathaway", "chubb", "progressive", "allstate", "aig",
            "metlife", "prudential", "combined ratio", "catastrophe loss",
            "asset manager", "blackrock", "blackstone", "kkr", "apollo global",
            "carlyle", "ares management", "private credit", "exchange operator",
            "cme group", "nasdaq inc", "visa", "mastercard", "paypal", "fintech",
            "stablecoin", "payments", "trading revenue", "investment banking fees",
            "fee pool",
        ],
        "tickers": ["JPM", "GS", "MS", "BAC", "BLK", "BX", "V", "SCHW"],
        "focus": (
            "Net interest margin and deposit costs, credit quality, capital "
            "rules, trading and banking fee pools, and private credit growth. "
            "FIG earnings are a direct function of the curve, so explicitly "
            "link the story to the rate environment covered earlier in the note."
        ),
    },
}

# ---------------------------------------------------------------------------
# Product groups
# ---------------------------------------------------------------------------

PRODUCT_GROUPS: dict[str, dict] = {
    "ma": {
        "title": "M&A",
        "subtitle": "Mergers & Acquisitions",
        "kind": "product",
        "accent": "#1D4ED8",
        "short": "M&A",
        "desk_note": (
            "Advises on buying and selling companies. Fees are a percentage of "
            "deal value, so volume and average deal size are the whole story."
        ),
        "keywords": [
            "acquire", "acquires", "acquisition", "merger", "merges", "takeover",
            "buyout", "tender offer", "definitive agreement", "all-cash deal",
            "all-stock deal", "stock-for-stock", "cash-and-stock", "deal value",
            "premium to", "unsolicited bid", "hostile bid", "raised its offer",
            "sweetened bid", "topping bid", "bidding war", "exclusive talks",
            "advanced talks", "strategic review", "spin-off", "spinoff",
            "carve-out", "divestiture", "divest", "sale process", "auction",
            "break fee", "termination fee", "antitrust review", "second request",
            "hart-scott", "deal collapse", "abandoned merger", "activist stake",
            "activist investor", "synergies", "accretive", "dilutive",
            "reverse merger",
        ],
        "tickers": [],
        "focus": (
            "The day's most consequential transaction. Name acquirer and target, "
            "deal value, consideration mix, premium to the unaffected price, and "
            "the implied multiple if derivable. Explain the strategic logic, who "
            "captures the value, the financing, the regulatory path, and what "
            "the deal signals about the sector's consolidation cycle. This is "
            "the section most likely to be asked about verbatim in an interview."
        ),
    },
    "ecm": {
        "title": "ECM",
        "subtitle": "Equity Capital Markets",
        "kind": "product",
        "accent": "#7E22CE",
        "short": "ECM",
        "desk_note": (
            "Raises equity: IPOs, follow-ons, converts, blocks. The IPO window "
            "opens and shuts with volatility, so watch VIX with the calendar."
        ),
        "keywords": [
            "ipo", "initial public offering", "public debut", "went public",
            "priced its ipo", "ipo priced", "price range", "raised the range",
            "cut the range", "shares priced at", "first day of trading",
            "trading debut", "direct listing", "s-1", "f-1", "filed to go public",
            "confidentially filed", "withdrew its ipo", "postponed its ipo",
            "follow-on offering", "secondary offering", "block trade",
            "at-the-market offering", "convertible notes offering",
            "convertible bond", "rights issue", "greenshoe", "over-allotment",
            "lock-up expiry", "spac", "de-spac", "listing", "underwriters",
            "bookrunner", "equity raise", "share sale", "stake sale",
        ],
        "tickers": [],
        "focus": (
            "The day's most important equity issuance event: an IPO pricing or "
            "debut, a filing, a withdrawal, a follow-on, a convert or a block. "
            "State size, price versus the indicated range, implied valuation, "
            "and first-day performance where available. Then read the broader "
            "signal: is the equity issuance window open, selective, or shut, "
            "and what does volatility imply for the forward calendar?"
        ),
    },
    "dcm": {
        "title": "DCM",
        "subtitle": "Investment Grade Debt Capital Markets",
        "kind": "product",
        "accent": "#0F766E",
        "short": "DCM",
        "desk_note": (
            "Raises investment grade debt for high-quality issuers. Volume is "
            "a function of the absolute yield level and spreads, not just rates."
        ),
        "keywords": [
            "investment grade bond", "investment-grade issuance", "bond offering",
            "notes offering", "senior notes", "corporate bond sale",
            "priced bonds", "bond sale", "debt offering", "issued bonds",
            "tapped the bond market", "new issue concession", "order book",
            "oversubscribed", "spread over treasuries", "basis points over",
            "credit spread", "ig spread", "coupon", "tranche", "maturity",
            "bridge loan", "term loan a", "revolving credit facility",
            "credit rating", "moody", "s&p global ratings", "fitch ratings",
            "downgrade to", "upgrade to", "outlook revised", "negative watch",
            "refinancing", "tender for notes", "debt maturity wall",
        ],
        "tickers": [],
        "focus": (
            "Investment grade supply and credit conditions. Cover the day's "
            "notable issuance, spread levels versus history, ratings actions, "
            "and the maturity wall. Explain the issuer's decision: why borrow "
            "now, at what all-in cost, and for what use of proceeds. Connect "
            "supply to the level of Treasury yields and IG spreads reported above."
        ),
    },
    "levfin": {
        "title": "Leveraged Finance",
        "subtitle": "High Yield & Leveraged Loans",
        "kind": "product",
        "accent": "#C2410C",
        "short": "LEV",
        "desk_note": (
            "Finances leveraged buyouts and speculative-grade borrowers. The "
            "cost of this debt sets whether sponsors can transact at all."
        ),
        "keywords": [
            "high yield", "high-yield bond", "junk bond", "leveraged loan",
            "term loan b", "tlb", "institutional loan", "loan repricing",
            "dividend recap", "recapitalization", "unitranche",
            "private credit deal", "direct lending", "clo",
            "collateralized loan", "covenant", "covenant-lite", "leverage ratio",
            "debt to ebitda", "turns of leverage", "hy spread", "oas",
            "payment-in-kind", "liability management", "amend and extend",
            "maturity extension", "distressed exchange", "second lien",
            "first lien", "sponsor financing", "acquisition financing",
            "committed financing", "staple financing",
        ],
        "tickers": [],
        "focus": (
            "Leveraged credit conditions and speculative-grade supply. Cover "
            "notable high yield or term loan B deals, leverage multiples, "
            "all-in yields, covenant quality, and private credit versus "
            "syndicated competition. Explain what today's cost of leveraged "
            "debt means for LBO math and sponsor activity."
        ),
    },
    "sponsors": {
        "title": "Sponsors",
        "subtitle": "Financial Sponsors & Private Equity",
        "kind": "product",
        "accent": "#4338CA",
        "short": "SPN",
        "desk_note": (
            "Covers private equity firms as clients. Watch dry powder, hold "
            "periods and the exit backlog, the recent binding constraint."
        ),
        "keywords": [
            "private equity", "buyout firm", "financial sponsor", "sponsor-backed",
            "blackstone", "kkr", "carlyle", "apollo", "tpg", "warburg pincus",
            "bain capital", "advent international", "cvc capital", "eqt ab",
            "thoma bravo", "vista equity", "silver lake", "general atlantic",
            "hellman & friedman", "clayton dubilier", "brookfield",
            "take-private", "taking it private", "club deal", "continuation fund",
            "continuation vehicle", "secondaries", "gp stake", "dry powder",
            "fund close", "fundraising", "limited partners", "distributions",
            "portfolio company exit", "sponsor exit", "hold period",
            "add-on acquisition", "bolt-on", "platform acquisition", "roll-up",
            "management buyout", "minority stake investment",
        ],
        "tickers": ["BX", "KKR", "APO", "CG", "ARES", "TPG"],
        "focus": (
            "Private equity activity: take-privates, exits, fundraising, "
            "continuation vehicles and add-ons. State equity check, leverage, "
            "entry multiple and the return math where derivable. Frame every "
            "story against the two structural constraints: record dry powder "
            "and a clogged exit backlog that limits distributions back to LPs."
        ),
    },
    "restructuring": {
        "title": "Restructuring",
        "subtitle": "Special Situations & Liability Management",
        "kind": "product",
        "accent": "#9F1239",
        "short": "RX",
        "desk_note": (
            "Advises distressed companies and their creditors. Countercyclical: "
            "busiest exactly when the other desks are quiet."
        ),
        "keywords": [
            "chapter 11", "chapter 7", "bankruptcy", "bankruptcy protection",
            "filed for bankruptcy", "insolvency", "administration", "receivership",
            "restructuring support agreement", "plan of reorganization",
            "debtor-in-possession", "dip financing", "creditor committee",
            "ad hoc group", "bondholder group", "distressed", "default",
            "missed interest payment", "grace period", "forbearance",
            "debt-for-equity", "equitization", "haircut", "recovery rate",
            "liability management exercise", "drop-down", "double dip",
            "uptier", "priming", "credit bid", "363 sale", "wind-down",
            "liquidation", "going concern", "covenant breach", "waiver",
            "cross-default", "selective default", "restructuring plan",
        ],
        "tickers": [],
        "focus": (
            "Distress and liability management. Cover filings, out-of-court "
            "exchanges, creditor-on-creditor violence, and recovery "
            "expectations. State the capital structure, the leverage, and where "
            "the fulcrum security sits when the reporting supports it. Tie the "
            "story to default rates and the maturity wall."
        ),
    },
}

# ---------------------------------------------------------------------------
# Standing sections that are not group-specific
# ---------------------------------------------------------------------------

STANDING_GROUPS: dict[str, dict] = {
    "geopolitical": {
        "title": "Geopolitics & Policy",
        "subtitle": "Trade, Sanctions & Regulation",
        "kind": "standing",
        "accent": "#B91C1C",
        "short": "GEO",
        "desk_note": "",
        "keywords": [
            "tariff", "tariffs", "trade war", "trade deal", "trade negotiation",
            "section 301", "section 232", "export control", "entity list",
            "sanction", "sanctions", "sanctioned", "embargo",
            "secondary sanctions", "china", "taiwan", "russia", "ukraine",
            "iran", "israel", "gaza", "middle east", "red sea",
            "strait of hormuz", "north korea", "nato", "european union",
            "brussels", "g7", "g20", "imf", "election", "government shutdown",
            "debt ceiling", "fiscal policy", "immigration policy", "antitrust",
            "ftc", "doj", "sec enforcement", "cfius", "national security review",
            "chip export", "rare earth", "critical minerals",
            "supply chain restriction", "emerging market", "sovereign debt",
            "currency intervention", "capital controls",
        ],
        "tickers": [],
        "focus": (
            "The single geopolitical or policy development with the clearest "
            "transmission channel into markets. Name the mechanism explicitly: "
            "which prices, which volumes, which companies, which margins. "
            "Quantify affected trade value or exposure where the reporting "
            "supports it. Skip anything without a concrete market angle."
        ),
    },
    "what_to_watch": {
        "title": "What to Watch",
        "subtitle": "Next 24 to 72 Hours",
        "kind": "standing",
        "accent": "#4F46E5",
        "short": "WCH",
        "desk_note": "",
        "keywords": [],
        "tickers": [],
        "focus": (
            "The four or five most consequential catalysts in the next 24 to 72 "
            "hours. For each: what it is, when it lands, the consensus "
            "expectation, what a beat would do, what a miss would do, and which "
            "market narrative it confirms or breaks."
        ),
    },
}

# ---------------------------------------------------------------------------
# Composite registries
# ---------------------------------------------------------------------------

ALL_GROUPS: dict[str, dict] = {
    **COVERAGE_GROUPS,
    **PRODUCT_GROUPS,
    **STANDING_GROUPS,
}

# Render order for the newsletter body
GROUP_ORDER: list[str] = (
    list(COVERAGE_GROUPS.keys())
    + list(PRODUCT_GROUPS.keys())
    + ["geopolitical", "what_to_watch"]
)

# Groups routed from the headline pool (What to Watch is built from calendars)
ROUTED_GROUPS: list[str] = [k for k in GROUP_ORDER if k != "what_to_watch"]

# Product-group keywords get a routing boost: a deal story should land on the
# product desk that would execute it, even when it also names an industry.
PRODUCT_KEYWORD_WEIGHT = 1.35
COVERAGE_KEYWORD_WEIGHT = 1.0

# A headline must clear this weighted score to be assigned to a group at all.
MIN_ROUTING_SCORE = 1.0


def group_meta(key: str) -> dict:
    return ALL_GROUPS.get(key, {})


def group_title(key: str) -> str:
    return ALL_GROUPS.get(key, {}).get("title", key.replace("_", " ").title())
