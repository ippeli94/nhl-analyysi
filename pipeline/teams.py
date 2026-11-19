"""NHL team abbreviation -> display name."""

from __future__ import annotations

import unicodedata

TEAM_NAMES: dict[str, str] = {
    "ANA": "Anaheim Ducks", "BOS": "Boston Bruins", "BUF": "Buffalo Sabres",
    "CAR": "Carolina Hurricanes", "CBJ": "Columbus Blue Jackets", "CGY": "Calgary Flames",
    "CHI": "Chicago Blackhawks", "COL": "Colorado Avalanche", "DAL": "Dallas Stars",
    "DET": "Detroit Red Wings", "EDM": "Edmonton Oilers", "FLA": "Florida Panthers",
    "LAK": "Los Angeles Kings", "MIN": "Minnesota Wild", "MTL": "Montréal Canadiens",
    "NJD": "New Jersey Devils", "NSH": "Nashville Predators", "NYI": "New York Islanders",
    "NYR": "New York Rangers", "OTT": "Ottawa Senators", "PHI": "Philadelphia Flyers",
    "PIT": "Pittsburgh Penguins", "SEA": "Seattle Kraken", "SJS": "San Jose Sharks",
    "STL": "St. Louis Blues", "TBL": "Tampa Bay Lightning", "TOR": "Toronto Maple Leafs",
    "UTA": "Utah Mammoth", "VAN": "Vancouver Canucks", "VGK": "Vegas Golden Knights",
    "WPG": "Winnipeg Jets", "WSH": "Washington Capitals",
}


def display_name(abbr: str, fallback: str) -> str:
    return TEAM_NAMES.get(abbr, fallback)


# Short place names for copy ("Carolina"); multi-word nicknames and the two New York clubs
# need help, everything else drops the last word.
_NICKNAMES = ("Maple Leafs", "Blue Jackets", "Golden Knights", "Red Wings")
_SHORT_OVERRIDES = {"NYI": "NY Islanders", "NYR": "NY Rangers"}
# Finnish genitive where the vowel rule is wrong ("Tampa Bayn").
_GENITIVE_OVERRIDES = {"Tampa Bay": "Tampa Bayn"}


def short_name(abbr: str, fallback: str) -> str:
    if abbr in _SHORT_OVERRIDES:
        return _SHORT_OVERRIDES[abbr]
    full = TEAM_NAMES.get(abbr)
    if full is None:
        return fallback
    for nick in _NICKNAMES:
        if full.endswith(" " + nick):
            return full[: -len(nick) - 1]
    return full.rsplit(" ", 1)[0]


def genitive(short: str) -> str:
    """Finnish genitive of a short team name: Carolina -> Carolinan, Boston -> Bostonin."""
    if short in _GENITIVE_OVERRIDES:
        return _GENITIVE_OVERRIDES[short]
    return short + ("n" if short[-1].lower() in "aeiouyäö" else "in")


ODDS_ALIASES: dict[str, str] = {"utah hockey club": "UTA"}


def _normalise(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.casefold().replace(".", "").split())


_ODDS_MAP: dict[str, str] = {_normalise(n): a for a, n in TEAM_NAMES.items()} | ODDS_ALIASES


def abbr_from_odds_name(name: str) -> str | None:
    """Map a bookmaker/Odds API team name to an NHL abbreviation, or None if unknown."""
    return _ODDS_MAP.get(_normalise(name))


def abbr_from_name(name: str) -> str | None:
    """Map a full team display name (ESPN, odds feeds; accents ignored) to an abbreviation."""
    return abbr_from_odds_name(name)
