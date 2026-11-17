"""ESPN injury report probe (optional source; status only in phase 1, spec 4.4)."""

from __future__ import annotations

import requests

from pipeline import config
from pipeline.sources.nhl import SourceError

URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries"
USER_AGENT = "nhl-analyysi/1.0"

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT


def fetch() -> list[dict]:
    """One GET of the ESPN injury report. The model does not use the result in phase 1."""
    try:
        response = _session.get(URL, timeout=config.TIMEOUT_S)
    except requests.RequestException as exc:
        raise SourceError(f"injuries request failed: {type(exc).__name__}") from exc
    if not response.ok:
        raise SourceError(f"injuries HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise SourceError("injuries response is not JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("injuries"), list):
        raise SourceError("injuries response has no injuries list")
    return payload["injuries"]
