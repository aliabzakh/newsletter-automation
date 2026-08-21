"""The verbatim content of the July 27, 2025 issue, plus its printed prices.

Used to render the layout without spending an API call, and as the visual
reference when comparing against reference/*.jpg.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

DATE_LONG = "July 27, 2025"

CONTENT = {
    "quote": {
        # The original prints "increase ... as motion increases", which garbles
        # Buffett — he said returns *decrease* as motion increases. Fixed here.
        "text": "For investors as a whole, returns decrease as motion increases.",
        "author": "Warren Buffett",
    },
    "capture_note": (
        "All figures are indicative, captured around the weekend close, "
        "expect gaps on Monday's open."
    ),
    "forex": [
        "Dollar steady into a data- & central-bank heavy week. The greenback held firm "
        "against the yen (~147.7) as investors brace for the Fed, BoJ and BoC meetings "
        "plus Trump's Aug. 1 tariff deadline. Gold hovered near $3,340.",
        "EUR/USD flat around 1.174 amid quiet weekend trade after Friday's modest bid on "
        "better risk sentiment tied to a flurry of US trade deals. AUD/USD softened to "
        "0.6570 area versus Friday, giving back late-week gains as markets reassess how "
        "much the RBA can diverge from the Fed.",
        "USD/CAD extends higher to 1.37 after crude slipped on Friday. Silver cools after "
        "a blistering run to decade highs, with profit-taking trimming prices below $39.",
    ],
    "international": [
        "Oil eases into the weekend: Brent settled -1.07% at $68.44 on Friday; WTI -1.32% "
        "at $65.16, as traders weighed inventory draws against lingering demand worries.",
        "JPMorgan cut its 2025 Brent forecast to $66 on weaker demand and higher OPEC+ "
        "output, warning prices could slip below $60 by year-end without fresh cuts.",
        "Asian stocks pulled back from highs and the USD firmed vs JPY ahead of a "
        "“crucial” macro week (Fed/BoJ/BoC, tariff deadline, Big Tech earnings)",
        "Markets ‘catch breath’ after a torrent of trade deals, with further US-EU "
        "talks pending; Australia opened its beef market to the US.",
        "Silver's surge continues to dominate metals chatter, up ~36% YTD and at the "
        "highest since 2011, driven by tight spot supply and tariff noise.",
        "World Bank warns of the weakest global growth run since 2008, projecting 2.3% "
        "world growth in 2025",
        "Cboe to exit Japan equities trading by year-end, showing tough competition in "
        "Asia's markets",
    ],
    "local": [
        "Central Bank dashboard: Inflation 2.0% (Jun), real GDP +2.7% (Q1 2025), foreign "
        "reserves $22.8bn (May); current-account deficit -7.7% of GDP (Q1).",
        "Exports rose to JD 2.5bn in the first third of 2025, led by a 133% jump in "
        "construction materials and double-digit gains across food, chemicals and packaging.",
    ],
    "on_this_day": [
        {"year": "1694", "text": "The Bank of England received its royal charter, formally "
                                 "creating one of the world's most influential central banks."},
        {"year": "2010", "text": "BP posted a $17.2 billion quarterly loss after booking "
                                 "$32.2 billion in charges tied to the Gulf of Mexico oil spill."},
    ],
}


def _quote(label, price, change, direction, decimals, unit):
    """A stand-in for market.Quote with the same surface the template touches."""
    price_text = f"{price:.{decimals}f}"
    change_text = f"{change:.0f}" if unit == "bps" else f"{change:.2f}"
    return SimpleNamespace(
        label=label, price=price, change_value=change, direction=direction,
        decimals=decimals, unit=unit, as_of=date(2025, 7, 25),
        price_text=price_text, change_text=change_text,
        display=f"{label}: {price_text} ({change_text})",
    )


MARKET = {
    "currencies": [
        _quote("EUR", 1.1744, 0,  "flat", 4, "bps"),
        _quote("GBP", 1.3439, 1,  "up",   4, "bps"),
        _quote("CAD", 1.3699, 58, "up",   4, "bps"),
        _quote("CHF", 0.7955, 1,  "up",   4, "bps"),
        _quote("AUD", 0.6569, 27, "down", 4, "bps"),
        _quote("JPY", 147.66, 9,  "up",   2, "bps"),
    ],
    "commodities": [
        _quote("XAU",   3337.18, 0.95, "down", 2, "pct"),
        _quote("XAG",   38.14,   2.35, "down", 2, "pct"),
        _quote("BRENT", 68.44,   1.07, "down", 2, "pct"),
        _quote("WTI",   65.16,   1.32, "down", 2, "pct"),
    ],
    "as_of": date(2025, 7, 25),
}
