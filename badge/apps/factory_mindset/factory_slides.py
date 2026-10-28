"""Native text and vector layouts; no raster assets or custom launcher icon."""

BACKGROUND = (13, 20, 16)
INK = (238, 245, 239)
GREEN = (102, 224, 137)
PURPLE = (181, 152, 255)
BLUE = (110, 182, 255)
PINK = (255, 128, 181)
MUTED = (158, 175, 165)
FONT_PATH = "/system/assets/fonts/MonaSans-Medium.af"

TALK = {
    "title": "The Factory Mindset",
    "date": "WED OCT 28, 2026",
    "time": "12:50-1:20 PM PDT",
    "venue": "Main Stage @ Festival Pavilion",
    "source": "https://reg.githubuniverse.com/flow/github/universe26/attendee-portal/page/sessioncatalog/session/1785161525956001tBix",
}

# Lines: text, x, y, font size, RGB, reveal delay in milliseconds.
# Boxes: x, y, width, height, RGB, reveal delay in milliseconds.
SLIDES = (
    {"lines": (
        (TALK["title"], 18, 16, 14, MUTED, 0),
        ("My laptop died.", 18, 66, 40, INK, 150),
        ("Programming", 18, 127, 39, GREEN, 1050),
        ("changed.", 18, 174, 39, GREEN, 1250),
    )},
    {"lines": (
        ("SUMMER 2025.", 18, 43, 32, INK, 100),
        ("TRAVELING.", 18, 94, 32, INK, 750),
        ("NO DEVELOPMENT", 33, 166, 20, GREEN, 1500),
        ("MACHINE.", 33, 190, 20, GREEN, 1500),
    ), "boxes": ((18, 155, 284, 65, GREEN, 1500),)},
    {"lines": (("A phone", 38, 148, 42, INK, 350),),
     "boxes": ((18, 25, 284, 190, GREEN, 100),), "symbol": "phone"},
    {"lines": (
        ("A cloud", 38, 123, 35, INK, 350),
        ("agent session", 38, 166, 35, INK, 850),
    ), "boxes": ((18, 25, 284, 190, PURPLE, 100),), "symbol": "agent"},
    {"lines": (
        ("A pull", 38, 123, 35, INK, 350),
        ("request", 38, 166, 35, INK, 850),
    ), "boxes": ((18, 25, 284, 190, BLUE, 100),), "symbol": "pull"},
    {"lines": (
        ("HOW FAR", 18, 37, 43, GREEN, 100),
        ("CAN YOU GO", 18, 96, 42, INK, 650),
        ("WITH THIS?", 18, 155, 42, INK, 1200),
    )},
    {"lines": (
        ("Fast forward", 18, 36, 39, INK, 100),
        ("1.5 years.", 18, 93, 44, GREEN, 650),
        ("The AW factory.", 18, 164, 32, INK, 1200),
    )},
    {"lines": (
        ("~23k+", 32, 35, 44, MUTED, 100),
        ("PULL REQUESTS", 170, 45, 14, MUTED, 450),
        ("github/gh-aw", 170, 67, 14, MUTED, 450),
        ("700+", 32, 139, 49, GREEN, 1050),
        ("CUSTOMER", 174, 144, 15, GREEN, 1400),
        ("ISSUES MERGED", 174, 167, 15, GREEN, 1400),
    ), "boxes": ((18, 25, 284, 85, MUTED, 100), (18, 130, 284, 85, GREEN, 1050))},
    {"lines": (
        ("6h", 32, 35, 49, PURPLE, 100),
        ("TIME TO", 174, 45, 16, PURPLE, 450),
        ("MERGE", 174, 68, 16, PURPLE, 450),
        ("100", 32, 140, 49, PINK, 1050),
        ("LoC", 174, 143, 24, PINK, 1400),
        ("SMALL", 174, 176, 14, PINK, 1400),
    ), "boxes": ((18, 25, 284, 85, PURPLE, 100), (18, 130, 284, 85, PINK, 1050))},
    {"lines": (
        ("50% started", 18, 36, 39, BLUE, 100),
        ("by humans.", 18, 87, 32, INK, 450),
        ("50% AW.", 18, 155, 47, PURPLE, 1050),
    )},
    {"lines": (
        ("Stop manually", 18, 32, 39, INK, 100),
        ("translating intent", 18, 94, 35, INK, 650),
        ("into code.", 18, 156, 41, INK, 1200),
    )},
    {"lines": (
        ("Engineer processes", 18, 33, 34, GREEN, 100),
        ("that create", 18, 94, 39, GREEN, 650),
        ("trusted features.", 18, 156, 35, GREEN, 1200),
    )},
    {"lines": (
        ("Markdown", 31, 24, 27, INK, 100),
        ("INTENT + GUARDRAILS", 31, 57, 12, BLUE, 100),
        ("GitHub Actions", 31, 97, 27, INK, 900),
        ("RUN THE WORKFLOW", 31, 130, 12, PURPLE, 900),
        ("Agents in a sandbox", 31, 170, 25, INK, 1700),
        ("CONTROLLED ENVIRONMENT", 31, 203, 12, GREEN, 1700),
    ), "boxes": ((18, 17, 284, 58, BLUE, 100),
                 (18, 90, 284, 58, PURPLE, 900),
                 (18, 163, 284, 58, GREEN, 1700)), "symbol": "workflow"},
    {"lines": (
        ("The Factory", 18, 24, 40, INK, 100),
        ("Mindset.", 18, 74, 43, GREEN, 550),
        ("Build a human-agent", 18, 141, 25, INK, 1000),
        ("process that works", 18, 171, 25, INK, 1200),
        ("for you.", 18, 201, 25, INK, 1400),
    )},
    {"lines": (
        ("The Factory", 18, 18, 39, INK, 100),
        ("Mindset", 18, 63, 39, GREEN, 350),
        (TALK["date"], 18, 120, 18, MUTED, 700),
        (TALK["time"], 18, 151, 30, GREEN, 1200),
        ("MAIN STAGE", 18, 194, 14, BLUE, 1700),
        ("FESTIVAL PAVILION", 18, 214, 14, BLUE, 1700),
    )},
)
