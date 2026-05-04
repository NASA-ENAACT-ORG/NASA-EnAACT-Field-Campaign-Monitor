"""
Shared registry for collector and route metadata.

This module is intended to be the single source of truth for identifiers,
labels, and groupings that are currently used across pipelines.
"""

# Route definitions
ROUTES_BY_BOROUGH: dict[str, tuple[str, ...]] = {
    "MN": ("HT", "WH", "UE", "MT", "LE"),
    "BX": ("HP", "NW"),
    "BK": ("DT", "WB", "BS", "CH", "SP", "CI"),
    "QN": ("FU", "LI", "JH", "JA", "FH", "LA", "EE"),
}

ALL_ROUTES: tuple[str, ...] = tuple(
    f"{borough}_{neigh}"
    for borough, neighborhoods in ROUTES_BY_BOROUGH.items()
    for neigh in neighborhoods
)

ROUTE_LABELS: dict[str, str] = {
    "MN_HT": "Manhattan - Harlem",
    "MN_WH": "Manhattan - Washington Hts",
    "MN_UE": "Manhattan - Upper East Side",
    "MN_MT": "Manhattan - Midtown",
    "MN_LE": "Manhattan - Union Sq / LES",
    "BX_HP": "Bronx - Hunts Point",
    "BX_NW": "Bronx - Norwood",
    "BK_DT": "Brooklyn - Downtown BK",
    "BK_WB": "Brooklyn - Williamsburg",
    "BK_BS": "Brooklyn - Bed Stuy",
    "BK_CH": "Brooklyn - Crown Heights",
    "BK_SP": "Brooklyn - Sunset Park",
    "BK_CI": "Brooklyn - Coney Island",
    "QN_FU": "Queens - Flushing",
    "QN_LI": "Queens - Astoria / LIC",
    "QN_JH": "Queens - Jackson Heights",
    "QN_JA": "Queens - Jamaica",
    "QN_FH": "Queens - Forest Hills",
    "QN_LA": "Queens - LaGuardia CC",
    "QN_EE": "Queens - East Elmhurst",
}
ROUTE_CODES: frozenset[str] = frozenset(ROUTE_LABELS)

# KML placemark names used by route parsing scripts
KML_NAME_TO_ROUTE: dict[str, str] = {
    "Harlem": "MN_HT",
    "Washington Heights": "MN_WH",
    "Upper East Side": "MN_UE",
    "Midtown": "MN_MT",
    "Union Square/LES": "MN_LE",
    "Norwood": "BX_NW",
    "Hunts Point": "BX_HP",
    "Downtown Brooklyn": "BK_DT",
    "Williamsburg": "BK_WB",
    "Bed Sty": "BK_BS",
    "Crown Heights": "BK_CH",
    "Sunset Park": "BK_SP",
    "Coney Island": "BK_CI",
    "Flushing": "QN_FU",
    "Astoria/LIC": "QN_LI",
    "Jackson Heights": "QN_JH",
    "Jamaica": "QN_JA",
    "Forest Hills": "QN_FH",
    "LaGuardia Community College": "QN_LA",
    "East Elmhurst": "QN_EE",
}

# Collector definitions
STUDENT_COLLECTORS: tuple[str, ...] = (
    "SOT", "AYA", "ALX", "TAH", "JAM", "JEN", "SCT", "TER",
)
LAST_RESORT_COLLECTORS: tuple[str, ...] = ("ANG",)
ACTIVE_COLLECTORS: tuple[str, ...] = STUDENT_COLLECTORS + LAST_RESORT_COLLECTORS
STAFF_COLLECTORS: tuple[str, ...] = ("PRA", "NAT", "NRS")
DASHBOARD_COLLECTORS: tuple[str, ...] = STUDENT_COLLECTORS + STAFF_COLLECTORS
NON_COLLECTOR_IDS: tuple[str, ...] = ("ANG",)
STUDENT_COLLECTOR_IDS: frozenset[str] = frozenset(STUDENT_COLLECTORS)
VALID_BACKPACKS: tuple[str, ...] = ("A", "B")
SLOT_TODS: tuple[str, ...] = ("AM", "MD", "PM")
LAST_RESORT_BACKPACK = "A"

COLLECTOR_DISPLAY_NAMES: dict[str, str] = {
    "SOT": "Soteri",
    "AYA": "Aya Nasri",
    "ALX": "Alex",
    "TAH": "Taha",
    "JAM": "James",
    "JEN": "Jennifer",
    "SCT": "Scott",
    "TER": "Terra",
    "ANG": "Angy",
    "PRA": "Prof. Prathap Ramamurthy",
    "NAT": "Prof. Nathan",
    "NRS": "Prof. Naresh Devineni",
}

# Names as they appear in Collector_Locs.kml
COLLECTOR_KML_NAME_TO_ID: dict[str, str] = {
    "Terra": "TER",
    "Aya": "AYA",
    "Scott": "SCT",
    "Alex": "ALX",
    "Jennifer": "JEN",
    "James": "JAM",
    "Taha": "TAH",
    "Soteri": "SOT",
    "Prof. Naresh Devineni": "NRS",
    "Prof. Prathap Ramamurthy": "PRA",
    "Angy": "ANG",
    "Unknown": "TAH",  # legacy fallback value found in older KML exports
}

# Collector ID -> first name as used in some historical KML workflows
COLLECTOR_KML_NAMES: dict[str, str] = {
    "SOT": "Soteri",
    "AYA": "Aya",
    "ALX": "Alex",
    "JAM": "James",
    "JEN": "Jennifer",
    "SCT": "Scott",
    "TER": "Terra",
    "ANG": "Angy",
    "TAH": "Taha",
    "NRS": "Prof. Naresh Devineni",
    "PRA": "Prof. Prathap Ramamurthy",
}

COLLECTOR_PIN_COLORS: dict[str, str] = {
    "SOT": "#7c3aed",
    "AYA": "#7c3aed",
    "JEN": "#7c3aed",
    "TAH": "#7c3aed",
    "ANG": "#7c3aed",
    "TER": "#dc2626",
    "ALX": "#dc2626",
    "SCT": "#dc2626",
    "JAM": "#dc2626",
}

# Backpack metadata
BACKPACK_COLLECTORS: dict[str, set[str]] = {
    "A": {"JEN", "AYA", "SOT", "TAH"},
    "B": {"TER", "ALX", "SCT", "JAM", "JEN"},
}
BACKPACK_AVAILABILITY_GROUPS: dict[str, tuple[str, ...]] = {
    "A": ("SOT", "AYA", "JEN", "TAH", "ANG"),
    "B": ("TER", "ALX", "SCT", "JAM", "JEN"),
}
BACKPACK_TO_STUDENT_COLLECTORS: dict[str, frozenset[str]] = {
    bp: frozenset(cid.upper() for cid in members)
    for bp, members in BACKPACK_COLLECTORS.items()
}
BACKPACK_TO_SCHEDULE_COLLECTORS: dict[str, frozenset[str]] = {
    bp: frozenset(
        set(collectors)
        | set(STAFF_COLLECTORS)
        | (set(LAST_RESORT_COLLECTORS) if bp == LAST_RESORT_BACKPACK else set())
    )
    for bp, collectors in BACKPACK_TO_STUDENT_COLLECTORS.items()
}
SCHEDULE_COLLECTOR_IDS: frozenset[str] = frozenset().union(
    *BACKPACK_TO_SCHEDULE_COLLECTORS.values()
)
COLLECTOR_GROUPS: tuple[dict[str, object], ...] = (
    {"id": "ccny", "cls": "ccny", "title": "CCNY", "sub": "Backpack A", "members": ("SOT", "AYA", "JEN", "TAH")},
    {"id": "lagcc", "cls": "lagcc", "title": "LaGCC", "sub": "Backpack B", "members": ("TER", "ALX", "SCT", "JAM")},
    {"id": "staff", "cls": "staff", "title": "Professors", "sub": "Non-scheduled", "members": ("NRS", "PRA", "NAT")},
)
CAMPUS_PROXY_ROUTE: dict[str, str] = {"A": "MN_HT", "B": "QN_LA"}

# Collector -> preferred route neighborhood codes used by dashboard analytics
COLLECTOR_ROUTE_AFFINITY: dict[str, tuple[str, ...]] = {
    "SOT": (),
    "AYA": ("MT", "LE", "DT", "WB", "BS", "CH", "SP", "CI"),
    "ALX": ("LE", "WB", "BS", "JA", "FH", "LA"),
    "TAH": ("HT", "MT", "LE", "FU", "LI", "JH", "JA", "FH", "LA", "EE"),
    "JAM": ("JH", "FH"),
    "JEN": ("HP", "HT", "WH", "UE", "MT", "LE", "DT", "WB", "BS", "FU", "LI", "JH", "FH", "LA", "EE"),
    "SCT": ("HT", "WH", "FU", "LI", "JH", "FH", "LA", "EE"),
    "TER": ("HT", "MT", "LE", "DT", "WB", "BS", "CH", "LI", "LA"),
    "PRA": (),
    "NAT": (),
    "NRS": (),
}

# Substring -> collector ID mapping for schedule filename matching
FILENAME_TO_COLLECTOR: dict[str, str] = {
    "terra": "TER",
    "emmerich": "TER",
    "aya": "AYA",
    "nasri": "AYA",
    "scott": "SCT",
    "atlixqueno": "SCT",
    "alex": "ALX",
    "leon": "ALX",
    "james": "JAM",
    "lu": "JAM",
    "jennifer": "JEN",
    "ramirez": "JEN",
    "soteri": "SOT",
    "pra": "PRA",
    "prathap": "PRA",
    "nat": "NAT",
    "natalie": "NAT",
    "nrs": "NRS",
    "tah": "TAH",
    "tahani": "TAH",
}
