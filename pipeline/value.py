"""No-vig market reference, best price, edge, EV and value picks (spec 6.11). Pure, no I/O."""

from __future__ import annotations

from statistics import median

from pipeline import config

_OUTCOMES = {
    "h2h": ("home", "away"),
    "1x2": ("home", "draw", "away"),
    "totals": ("over", "under"),
    "spreads": ("home", "away"),
}
_MARKET_ORDER = ("h2h", "1x2", "totals", "spreads")
_P = 4


def novig(prices: list[float]) -> tuple[list[float], float]:
    """Proportional no-vig probabilities and the vig (sum of implied probabilities - 1)."""
    q = [1.0 / x for x in prices]
    total = sum(q)
    return [x / total for x in q], total - 1.0


def effective_price(book: str, price: float) -> float:
    """Price after the exchange commission on winnings."""
    return 1.0 + (price - 1.0) * (1.0 - config.ODDS_COMMISSION.get(book, 0.0))


def clv(price: float, p_close: float) -> float:
    return price * p_close - 1.0


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, _P)


def model_prob(
    game: dict, market: str, line: float | None, outcome: str
) -> tuple[float, float] | None:
    """(p_win, p_push) of an outcome from the model, or None when it cannot be priced."""
    internal = game.get("_internal")
    if internal is None:
        return None
    if market == "h2h":
        key = {"home": "home_win", "away": "away_win"}.get(outcome)
        return (game["prob"][key], 0.0) if key else None
    if market == "1x2":
        key = {"home": "reg_home", "draw": "reg_draw", "away": "reg_away"}.get(outcome)
        return (game["prob"][key], 0.0) if key else None
    if market == "totals":
        dist = internal.get("total_dist")
        if not dist or line is None or outcome not in ("over", "under"):
            return None
        if not 0 <= line < len(dist) - 1:
            return None
        frac = line % 1
        if frac not in (0.0, 0.5):
            return None
        over = sum(p for k, p in enumerate(dist) if k > line)
        under = sum(p for k, p in enumerate(dist) if k < line)
        push = dist[int(line)] if frac == 0 else 0.0
        return (over if outcome == "over" else under, push)
    if market == "spreads":
        spread = game.get("score", {}).get("spread")
        if not spread or line not in (-1.5, 1.5):
            return None
        keys = {
            (-1.5, "home"): "home_-1.5", (-1.5, "away"): "away_+1.5",
            (1.5, "home"): "home_+1.5", (1.5, "away"): "away_-1.5",
        }
        key = keys.get((line, outcome))
        return (spread[key], 0.0) if key else None
    return None


def _book_order(books: dict) -> list[str]:
    known = [b for b in config.ODDS_BOOKMAKERS if b in books]
    return known + sorted(b for b in books if b not in config.ODDS_BOOKMAKERS)


def _sort_key(k: tuple[str, float | None]) -> tuple[int, float]:
    return _MARKET_ORDER.index(k[0]), k[1] if k[1] is not None else 0.0


def _collect(books: dict) -> dict[tuple[str, float | None], dict[str, list[float]]]:
    """(market, line) -> {book: prices in outcome order}."""
    out: dict[tuple[str, float | None], dict[str, list[float]]] = {}
    for book in _book_order(books):
        bm = books[book]
        if bm.get("h2h"):
            out.setdefault(("h2h", None), {})[book] = list(bm["h2h"])
        if bm.get("h2h3"):
            out.setdefault(("1x2", None), {})[book] = list(bm["h2h3"])
        for line, over, under in bm.get("totals") or []:
            out.setdefault(("totals", float(line)), {})[book] = [over, under]
        for point, home, away in bm.get("spreads") or []:
            out.setdefault(("spreads", float(point)), {})[book] = [home, away]
    return out


def _reference(entries: dict[str, list[float]]) -> tuple[dict, str | None, list[float] | None]:
    """Per-book analysis, reference kind and the reference no-vig probabilities."""
    lo, hi = config.ODDS_VIG_RANGE
    info = {}
    for book, prices in entries.items():
        p, vig = novig(prices)
        info[book] = {"p": p, "vig": vig, "valid": lo <= vig <= hi}
    ref = info.get(config.ODDS_REF_BOOK)
    if ref and ref["valid"]:
        return info, "pinnacle", ref["p"]
    valid = [i["p"] for i in info.values() if i["valid"]]
    if len(valid) >= config.ODDS_REF_MIN_BOOKS:
        med = [median(col) for col in zip(*valid, strict=True)]
        s = sum(med)
        return info, "median", [x / s for x in med]
    return info, None, None


def _veikkaus(entries, idx, p_win, p_push, p_model, p_market):
    prices = entries.get(config.VEIKKAUS_BOOK)
    if prices is None:
        return None
    price = prices[idx]
    ev = edge = None
    if p_model is not None and p_market is not None:
        ev = p_win * effective_price(config.VEIKKAUS_BOOK, price) + p_push - 1.0
        edge = p_model - p_market
    value = bool(ev is not None and ev >= config.ODDS_EV_MIN and edge >= config.ODDS_EDGE_MIN)
    return {"price": price, "ev": _r(ev), "value": value}


def _market(game: dict, key: str, line: float | None, entries: dict) -> dict:
    info, ref, p_ref = _reference(entries)
    outcomes = []
    for idx, name in enumerate(_OUTCOMES[key]):
        mp = model_prob(game, key, line, name)
        p_win, p_push = mp if mp else (None, 0.0)
        p_model = None
        if p_win is not None and p_push < 1.0:
            p_model = p_win / (1.0 - p_push)
        p_market = p_ref[idx] if p_ref else None

        best_book, best_price, best_eff = None, None, 0.0
        for book, prices in entries.items():
            if not info[book]["valid"]:
                continue
            eff = effective_price(book, prices[idx])
            if eff > best_eff:
                best_book, best_price, best_eff = book, prices[idx], eff
        best = {"book": best_book, "price": best_price} if best_book else None

        ev = edge = None
        if p_model is not None and p_market is not None:
            edge = p_model - p_market
            if best:
                ev = p_win * best_eff + p_push - 1.0
        value = bool(
            ev is not None and ev >= config.ODDS_EV_MIN and edge >= config.ODDS_EDGE_MIN
        )
        flag = "suspicious" if edge is not None and abs(edge) > config.ODDS_SUSPICIOUS_GAP else None
        outcomes.append({
            "key": name,
            "p_model": _r(p_model),
            "p_market": _r(p_market),
            "best": best,
            "veikkaus": _veikkaus(entries, idx, p_win, p_push, p_model, p_market),
            "edge": _r(edge),
            "ev": _r(ev),
            "value": value,
            "flag": flag,
        })
    return {
        "key": key,
        "line": line,
        "ref": ref,
        "outcomes": outcomes,
        "books": [
            {"book": b, "prices": entries[b], "vig": _r(info[b]["vig"]), "valid": info[b]["valid"]}
            for b in entries
        ],
    }


def game_odds(game: dict, books: dict, fetched_at: str) -> dict | None:
    if not books:
        return None
    collected = _collect(books)
    markets = [
        _market(game, key, line, collected[(key, line)])
        for key, line in sorted(collected, key=_sort_key)
    ]
    picks = []
    for m in markets:
        for o in m["outcomes"]:
            if o["value"]:
                picks.append({
                    "market": m["key"], "line": m["line"], "outcome": o["key"],
                    "book": o["best"]["book"], "price": o["best"]["price"],
                    "p_model": o["p_model"], "p_market": o["p_market"],
                    "edge": o["edge"], "ev": o["ev"], "flag": o["flag"],
                    "veikkaus": o["veikkaus"],
                })
    picks.sort(key=lambda p: (p["flag"] is not None, -p["ev"]))
    return {"fetched_at": fetched_at, "markets": markets, "picks": picks}


def market_summary(odds: dict | None) -> dict | None:
    if not odds:
        return None
    by_key = {m["key"]: m for m in odds["markets"] if m["key"] in ("h2h", "1x2")}

    def probs(key):
        m = by_key.get(key)
        if not m or any(o["p_market"] is None for o in m["outcomes"]):
            return None
        return [o["p_market"] for o in m["outcomes"]]

    h2h = by_key.get("h2h")
    best = None
    if h2h and all(o["best"] for o in h2h["outcomes"]):
        best = {o["key"]: dict(o["best"]) for o in h2h["outcomes"]}
    ref_src = (h2h or by_key.get("1x2") or {}).get("ref")
    return {
        "fetched_at": odds["fetched_at"],
        "h2h": probs("h2h"),
        "1x2": probs("1x2"),
        "ref": ref_src,
        "best_h2h": best,
    }


def ref_summary(books: dict) -> dict:
    collected = _collect(books)
    out = {"source": None, "h2h": None, "1x2": None, "totals": {}, "spreads": {}}
    for key, line in sorted(collected, key=_sort_key):
        _, ref, p = _reference(collected[(key, line)])
        if p is None:
            continue
        if out["source"] is None or key == "h2h":
            out["source"] = ref
        rounded = [round(x, _P) for x in p]
        if key in ("h2h", "1x2"):
            out[key] = rounded
        elif key == "totals":
            out["totals"][str(line)] = rounded[0]
        else:
            out["spreads"][str(line)] = rounded[0]
    return out


def closing_lines(rows: list[dict]) -> dict[int, dict]:
    """Per game the ref of the last odds-history row strictly before puck drop."""
    best: dict[int, dict] = {}
    for row in rows:
        if row["ts"] >= row["start_utc"]:
            continue
        cur = best.get(row["game_id"])
        if cur is None or row["ts"] > cur["ts"]:
            best[row["game_id"]] = row
    return {gid: r["ref"] for gid, r in best.items()}
