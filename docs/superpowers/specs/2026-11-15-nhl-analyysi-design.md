# NHL-otteluanalyysi — suunnitteludokumentti

- **Päiväys:** 2026-11-15
- **Tila:** Suunnitelma valmis toteutussuunnittelua varten. Koodia ei ole vielä kirjoitettu.
- **Suunnittelija:** Fable 5.2 (toteutus halvemmilla malleilla, ks. luku 12)
- **Merkintä:** *Suunnittelijan valinta* = päätös, jonka suunnittelija teki itse; käyttäjä voi kumota sen. Muut päätökset ovat käyttäjän tekemiä tai suoraan hänen toiveistaan johdettuja.
- **Varmennustaso:** Luvun 5 lähdetaulukossa kerrotaan, mitkä rajapinnat tarkistettiin tätä dokumenttia tehdessä ja mitkä ovat muistinvaraisia. Kaikki mallin lukuarvot (puoliintumisajat, painot, kynnykset) ovat **lähtöarvoja**, jotka viritetään backtestillä — niitä ei ole mitattu.

---

## 1. Tarkoitus

Henkilökohtainen, täysin automaattinen NHL-otteluiden analyysityökalu, jota käyttäjä katsoo puhelimella vedonlyöntipäätösten tueksi. Käyttäjä ei syötä mitään käsin: kaikki data haetaan NHL:n ja muiden julkisten lähteiden rajapinnoista, malli simuloi ottelut ja sivu näyttää tuloksen suomeksi.

**Miksi:** Käyttäjä haluaa yhdestä paikasta (a) oman, vedonvälittäjistä riippumattoman todennäköisyysarvion, (b) vertailun kertoimiin, jotta ylikertoimet löytyvät, ja (c) perustelut ("nostot"), joista näkee miksi malli päätyy arvioonsa. Tärkein laatuvaatimus ei ole hienous vaan **luotettavuus**: käyttäjän on nähtävä, kuinka hyvin malli on oikeasti osunut ja kuinka tuoretta data on.

### 1.1 Tavoitteet

Jokaisesta tulevasta ottelusta näytetään:

1. Voittotodennäköisyydet ja todennäköisin lopputulos Monte Carlo -simulaatiosta.
2. Kummankin joukkueen maaliodotus (xG) juuri tässä ottelussa.
3. Top 5 pelaajaa, jotka todennäköisimmin tekevät 1 / 2 / 3+ pistettä ja 1 / 2 / 3+ maalia.
4. Tekstinostot: eräkohtaiset vahvuudet, momentum, erikoistilanteet, back-to-back, putket.
5. Vertailu viime kauteen ja aiempiin keskinäisiin kohtaamisiin.
6. Loukkaantumisten, kauppojen ja kokoonpanomuutosten vaikutus ennusteeseen.
7. Viimeisen 10 ottelun vire.
8. Kerroinvertailu (The Odds API): mallin todennäköisyys vs. marginaaliton markkinatodennäköisyys, etu (edge) ja odotusarvo (EV) — "ylikertoimet"-näkymä.
9. Mallin osumatarkkuus ja datalähteiden tila näkyvissä sovelluksessa.

Lisäksi: mobiili edellä, visuaalisesti erittäin hyvä, koodi yksinkertaista, ajot automaattisia, ja erillinen tarkistusskripti, joka kirjoittaa lyhyen ongelmaraportin.

### 1.2 Ei-tavoitteet

- Ei automaattista vedon asettamista eikä kirjautumista vedonvälittäjille.
- Ei live-mallia ottelun aikana (ennuste jäädytetään ottelun alkaessa).
- Ei maksullista dataa eikä omaa palvelinta.
- Ei pelaajakohtaisten vetojen kerroinvertailua v1:ssä (pelaajakertoimia on lähinnä yhdysvaltalaisilla välittäjillä; pelaajaennusteet ovat aluksi vain mallin lukuja).
- Ei käyttäjätilejä, ei omaa vetokirjanpitoa, ei ilmoituksia v1:ssä.
- Ei pudotuspelikohtaista mallia v1:ssä (sama malli toimii, mutta sarjatilannetta ei mallinneta).

### 1.3 Anti-tavoitteet (mitä ei saa syntyä)

| Anti-tavoite | Miten estetään |
|---|---|
| Ylivarmat, kalibroimattomat todennäköisyydet | Parametriepävarmuus mukana simulaatiossa (6.9), kalibrointiseuranta ja -käyrä näkyvissä (luku 8), "epäilyttävän suuri ero markkinaan" -varoitus (6.11), varmuusleima pienellä otoksella |
| Hiljainen hajoaminen lähteen muuttuessa | Jokaisella lähteellä tila (ok / vanha / epäonnistui), skeemavalidointi haun yhteydessä, tarkistusskripti jokaisessa ajossa, GitHubin sähköposti epäonnistuneesta ajosta |
| Käyttäjä ei voi luottaa, koska datan ikä on piilossa | Päivitysaika joka näkymän yläreunassa, punainen banneri kun data on yli 14 h vanhaa, lähdekohtaiset aikaleimat, maalivahti- ja loukkaantumistiedoissa leima "vahvistettu / arvio / päätelty" |

Huomio käyttäjän esimerkistä "Carolina 85 %": NHL:ssä yksittäisen ottelun todelliset voittotodennäköisyydet ovat harvoin yli noin 75 %. Jos malli näyttää 85 %, se on lähes varmasti merkki virheestä, ei löydöstä. Malli ei saa tuottaa tällaisia lukuja ilman varoitusta.

---

## 2. Rajoitteet

| Rajoite | Vaikutus suunnitteluun |
|---|---|
| The Odds API:n ilmaistaso, noin 500 krediittiä/kk | Kertoimet haetaan enintään 2 kertaa päivässä; hinta = markkinat × alueet. Ks. 6.11 budjettilaskelma. |
| GitHub Actions | Julkisessa repossa minuutit ovat ilmaisia. Cron voi myöhästyä kymmeniä minuutteja. Ajastetut workflow't pysähtyvät, jos repossa ei ole toimintaa 60 päivään — pipeline tekee datakommitteja, mikä pitää repon aktiivisena kauden aikana; kesätauon jälkeen workflow voi vaatia uudelleenkäynnistyksen käsin. |
| GitHub Pages | Vain staattisia tiedostoja. Ilmaistilillä Pages vaatii **julkisen repon** → koodi ja data ovat julkisia. API-avain on vain GitHub Secretissä, ei koskaan lokeissa eikä JSONissa. |
| NHL:n rajapinta on epävirallinen ja dokumentoimaton | Voi muuttua ilman varoitusta. Kohtelias hakutahti (enintään noin 3 pyyntöä/s, uudelleenyritys viiveellä), skeemavalidointi. |
| Loukkaantumis- ja maalivahtilähteet ovat hauraita | Ne ovat valinnaisia lähteitä: ajo ei saa kaatua niihin, ja varatapa on päätellä tiedot kokoonpanoista (6.5, 6.6). |
| Käyttöliittymän kieli | Suomi. Kellonajat Suomen aikaa (Europe/Helsinki). Desimaalikertoimet. |
| Yksinkertaisuus | Ei tietokantaa, ei frontend-buildia, ei frameworkia. Python + numpy/pandas/scikit-learn; sivu on puhdasta HTML/CSS/JS:ää. |

---

## 3. Lähestymistavat

| | A. GitHub Actions + Pages (valittu) | B. Serverless live-sovellus | C. Ajo käyttäjän omalla PC:llä |
|---|---|---|---|
| Kuvaus | Python-pipeline ajetaan cronilla 3×/vrk, kirjoittaa JSONin; staattinen sivu lukee sen | Pilvifunktiot hakevat ja laskevat pyynnöstä | Ajastettu skripti kotikoneella |
| Kustannus | 0 € | Ilmaistasot riittävät ehkä, mutta kerroinkrediitit palavat | 0 € |
| Monimutkaisuus | Pieni | Suurin (tila, välimuisti, kylmäkäynnistykset) | Pieni |
| Saatavuus puhelimella | Aina | Aina | Vain kun kone on päällä ja verkossa |
| Heikkous | Data on enintään muutaman tunnin vanhaa | Krediitit, monimutkaisuus | Ei toimi poissa kotoa |

**Valinta (käyttäjän): A.** Perustelu: ottelut pelataan Suomen aikaa yöllä, joten käyttäjä tekee päätöksensä illalla — muutaman tunnin välein päivittyvä data riittää. Ei palvelinta, ei kustannuksia, ja epäonnistunut ajo jättää edellisen toimivan sivun näkyviin.

---

## 4. Arkkitehtuuri

### 4.1 Kokonaiskuva

```
GitHub Actions (cron 3×/vrk)
  └─ python -m pipeline.run
       1 fetch      NHL API, kertoimet, loukkaantumiset, maalivahdit
       2 cache      uudet päättyneet ottelut → pysyvä välimuisti (kommitoidaan)
       3 features   joukkue-, pelaaja-, maalivahti- ja erätaulut välimuistista
       4 model      joukkuevahvuudet + ottelukohtaiset kertoimet → λ per joukkue
       5 simulate   10 000 simulaatiota per ottelu
       6 players    pelaajien piste- ja maalitodennäköisyydet
       7 odds       marginaalin poisto, edge, EV
       8 insights   sääntöpohjaiset nostot
       9 tracking   ennusteiden tallennus, tulosten kirjaus, tarkkuusmittarit
      10 export     site/data/*.json
  └─ python tools/review.py   → reports/review.md (vain ongelmat)
  └─ git commit (data/ + reports/)  →  Pages deploy (site/)
Puhelin → GitHub Pages → index.html + data/latest.json
```

### 4.2 Repon rakenne

```
.github/workflows/
  pipeline.yml        cron + workflow_dispatch; aja → tarkista → kommitoi → julkaise
  backfill.yml        käsin käynnistettävä: historian lataus (kerran)
  train_xg.yml        käsin / kerran kuussa: xG-mallin uudelleensovitus
pipeline/
  run.py              vaiheiden ajo järjestyksessä, lähdekohtainen virheenkäsittely
  config.py           KAIKKI parametrit ja kynnykset yhdessä paikassa
  sources/            nhl.py, odds.py, injuries.py, goalies.py  (vain haku + validointi)
  cache.py            ottelukohtaisen välimuistin luku/kirjoitus
  xg.py               laukauspiirteet + xG-pisteytys (kertoimet model/xg_coefs.json)
  features.py         taulut välimuistista
  ratings.py          joukkuevahvuudet (painotettu Poisson-regressio)
  adjust.py           maalivahti-, kokoonpano- ja H2H-kertoimet
  simulate.py         Monte Carlo
  players.py          pelaajatodennäköisyydet
  value.py            kerroinlaskenta
  insights.py         sääntömoottori (säännöt datana, tekstit suomeksi)
  tracking.py         ennusteloki, tulokset, mittarit
  export.py           JSON-sopimuksen mukainen ulostulo
  teams.py            joukkueiden nimi- ja lyhennekartta (NHL ↔ Odds API ↔ ESPN)
model/xg_coefs.json   sovitetut xG-kertoimet (kommitoitu)
data/
  cache/games/{kausi}/{gameId}.json.gz    muuttumaton, yksi per päättynyt ottelu
  history/predictions/{kausi}.jsonl       lisäys loppuun
  history/results/{kausi}.jsonl
  history/odds/{kausi}.jsonl              haetut kertoimet (ROI- ja CLV-laskentaa varten)
  state/sources.json                      lähteiden viimeisin onnistunut haku
  state/rosters.json                      edellisen ajon kokoonpanot (muutosten havaitsemiseen)
site/
  index.html, app.js, styles.css, manifest.webmanifest
  data/               generoidaan ajossa, EI kommitoida (julkaistaan Pages-artefaktina)
tools/review.py
reports/review.md     viimeisin tarkistusraportti (kommitoidaan)
tests/                pytest + pienet kiinteät esimerkkivastaukset (fixtures)
docs/
```

*Suunnittelijan valinnat:*
- **Välimuisti kommitoidaan repoon ottelukohtaisina muuttumattomina tiedostoina.** Yksi iso kausitiedosto kirjoitettaisiin joka päivä uudelleen ja paisuttaisi git-historiaa; ottelukohtainen tiedosto kirjoitetaan kerran. Actions-välimuisti ei kelpaa, koska se poistuu käyttämättömänä.
- **`site/data/` ei mene gitiin**, vain Pages-artefaktiin — ei turhaa historiaa. Pysyvä tieto (ennusteet, tulokset, kertoimet) on `data/history/`-lokeissa.
- **Ei frontend-frameworkia eikä build-vaihetta.**

### 4.3 Ajastus

NHL-ottelut alkavat tyypillisesti klo 02:00 Suomen aikaa (19:00 ET); viikonlopun päiväpelit noin klo 19–22.

| Ajo (UTC) | Suomen aikaa (talvi) | Sisältö | Kertoimet |
|---|---|---|---|
| 06:43 | 08:43 | Yön tulokset välimuistiin, tarkkuusmittarit, seuraavan yön ensimmäiset ennusteet | Ei |
| 15:17 | 17:17 | Loukkaantumiset, kokoonpanot, ennusteet | Kyllä |
| 21:37 | 23:37 | Viimeinen päivitys ennen otteluita: maalivahdit, kertoimet | Kyllä |

*Suunnittelijan valinta:* kolme ajoa, joista kaksi hakee kertoimet. Minuutit eivät ole tasatunnilla, koska GitHubin cron ruuhkautuu silloin. Workflow'ssa `concurrency`-ryhmä estää päällekkäiset ajot. Jo alkaneita otteluita ei lasketa uudelleen: **virallinen ennuste = viimeinen ajo ennen ottelun alkua**, ja vain se lasketaan tarkkuusmittareihin.

"Peli-ilta" ryhmitellään NHL:n päivämäärän mukaan (ottelu, joka alkaa klo 02:00 Suomen aikaa 16.11., kuuluu NHL-päivään 15.11.), ja käyttöliittymässä otsikko on "Ensi yön ottelut".

### 4.4 Virheenkäsittelyn periaate

- **Ydinlähde (NHL API) epäonnistuu** → ajo keskeytyy, mitään ei julkaista, edellinen sivu jää näkyviin ja sen ikäbanneri kertoo tilanteen. Workflow päättyy virheeseen → GitHub lähettää sähköpostin.
- **Valinnainen lähde (kertoimet, loukkaantumiset, maalivahdit) epäonnistuu** → ajo jatkuu, lähteen tila merkitään `failed` tai `stale`, käytetään viimeisintä onnistunutta dataa (jos alle 36 h vanhaa) tai varatapaa, ja käyttöliittymä näyttää tilan.
- Jokainen lähdevastaus validoidaan (vaaditut kentät olemassa) ennen käyttöä. Validointivirhe = lähde epäonnistui.

### 4.5 JSON-sopimus frontendille

Kaksi tiedostoa. *Suunnittelijan valinta:* kaikki tulevat ottelut yhdessä tiedostossa (enintään noin 16 ottelua, pakattuna kymmeniä kilotavuja) — yksi haku, yksinkertainen frontend.

**`site/data/latest.json`** (luonnos; kentät englanniksi, tekstit suomeksi):

```jsonc
{
  "schema_version": 1,
  "generated_at": "2026-11-15T21:41:07Z",
  "model_version": "1.0.0",
  "sources": [
    {"id": "nhl",      "status": "ok",     "fetched_at": "...", "note": null},
    {"id": "odds",     "status": "ok",     "fetched_at": "...", "note": "Krediittejä jäljellä 312"},
    {"id": "injuries", "status": "failed", "fetched_at": "2026-11-14T15:20:00Z", "note": "ESPN 403 — käytetään kokoonpanoista pääteltyä tietoa"},
    {"id": "goalies",  "status": "ok",     "fetched_at": "...", "note": null}
  ],
  "review": {"problems": 0, "worst": null},
  "games": [{
    "id": 2026020321,
    "start_utc": "2026-11-16T00:00:00Z",
    "nhl_date": "2026-11-15",
    "state": "upcoming",                      // upcoming | started (jäädytetty)
    "home": {"abbr": "CAR", "name": "Carolina Hurricanes", "logo": "https://…svg"},
    "away": {"abbr": "SEA", "name": "Seattle Kraken", "logo": "https://…svg"},
    "prob": {
      "home_win": 0.642, "away_win": 0.358,                 // sis. jatkoaika ja voittolaukaukset
      "reg_home": 0.531, "reg_draw": 0.222, "reg_away": 0.247,   // 1X2, varsinainen peliaika
      "confidence": "normal"                                // normal | low (kauden alku, puuttuva data)
    },
    "xg": {"home": 3.42, "away": 2.61},
    "score": {
      "most_likely": {"home": 3, "away": 2, "p": 0.094},
      "top": [{"home": 3, "away": 2, "p": 0.094}, …],       // 5 todennäköisintä
      "matrix": [[…]],                                      // 0–7 × 0–7, lopputulos
      "total": {"mean": 6.1, "over": {"5.5": 0.57, "6.5": 0.39}},
      "spread": {"home_-1.5": 0.38, "away_+1.5": 0.62}
    },
    "periods": [{"n": 1, "xg_home": 1.05, "xg_away": 0.84, "p_home_wins_period": 0.41, "p_draw": 0.33}, …],
    "factors": [                                            // mistä λ koostuu — läpinäkyvyys
      {"key": "base",    "label": "Joukkuevahvuus",     "home": 1.00, "away": 1.00},
      {"key": "home_ice","label": "Kotietu",            "home": 1.05, "away": 1.00},
      {"key": "rest",    "label": "Lepo / back-to-back","home": 1.00, "away": 0.96},
      {"key": "goalie",  "label": "Maalivahti",         "home": 1.02, "away": 0.97},
      {"key": "lineup",  "label": "Poissaolot ja kaupat","home": 0.95, "away": 1.00},
      {"key": "h2h",     "label": "Keskinäiset",        "home": 1.01, "away": 0.99}
    ],
    "goalies": {
      "home": {"id": 8480045, "name": "…", "status": "confirmed", "gsax_per60": 0.21},
      "away": {"id": …,       "name": "…", "status": "projected", "gsax_per60": -0.05}
    },                                                      // confirmed | likely | projected
    "lineup_changes": [
      {"team": "CAR", "player": "…", "kind": "out", "source": "espn", "detail": "alavartalo, päivittäin arvioitava",
       "since": "2026-11-10", "impact_attack": -0.031, "impact_defense": 0.0}
    ],                                                      // kind: out | doubtful | returning | acquired | traded_away
    "players": [
      {"id": …, "name": "…", "team": "CAR", "pos": "C", "toi_proj": 19.4,
       "p_points": [0.61, 0.24, 0.07], "p_goals": [0.33, 0.06, 0.01]}   // ≥1, ≥2, ≥3
    ],                                                      // 10 parasta per joukkue
    "form": {"home": {"last10": "7-2-1", "seq": "VVHVVVJVHV", "gf": 34, "ga": 22, "xgf_pct": 0.56, "streak": "V3"},
             "away": {…}},
    "season_compare": {"home": {"now": {"pts_pct": 0.68, "gf_pg": 3.4, "ga_pg": 2.5, "xgf_pct": 0.56, "pp": 0.24, "pk": 0.83},
                                "last": {…}}, "away": {…}},
    "h2h": {"games": [{"date": "2026-03-02", "home": "SEA", "score": "2-4", "xg": "2.1-3.3"}, …],
            "summary": "CAR 5–1 kuudessa viime kohtaamisessa"},
    "insights": [{"id": "period_strength", "team": "CAR", "severity": 2,
                  "text": "Carolina on vahva 1. ja 3. erässä, mutta 2. erässä vastustajat luovat enemmän (xG-osuus 46 %)."}],
    "odds": {
      "fetched_at": "…",
      "h2h": [{"book": "pinnacle", "home": 1.58, "away": 2.52, "novig_home": 0.615}, {"book": "veikkaus_fi", …}],
      "market": {"home": 0.615, "source": "pinnacle"},      // vertailutaso
      "totals": [{"book": "…", "line": 6.0, "over": 1.95, "under": 1.87}],
      "value": [{"market": "h2h", "pick": "CAR", "book": "coolbet", "odds": 1.66,
                 "p_model": 0.642, "p_market": 0.615, "edge": 0.027, "ev": 0.066, "flag": null}]
    }                                                       // odds = null jos ei saatavilla
  }]
}
```

**`site/data/performance.json`**: mittarit (luku 8) valmiiksi laskettuina + viimeisimmät 50 ratkennutta ennustetta.

Sopimuksen säännöt: todennäköisyydet desimaalilukuina 0–1; puuttuva tieto on `null`, ei koskaan keksitty arvo; `schema_version` kasvaa rikkovissa muutoksissa ja frontend näyttää selkeän virheen, jos versio on tuntematon.

---

## 5. Datalähteet

| Lähde | Osoite | Käyttö | Varmennus |
|---|---|---|---|
| NHL web API | `https://api-web.nhle.com/v1/schedule/{pvm}` (myös `/schedule/now`) | Tulevat ottelut, alkamisajat, `gameState` | Haettu ja tarkistettu 15.11.2026 |
| | `/v1/gamecenter/{gameId}/play-by-play` | Laukaukset: `details.xCoord`, `yCoord`, `shotType`, `zoneCode`, `shootingPlayerId` / `scoringPlayerId`, `goalieInNetId`, `eventOwnerTeamId`; lisäksi `situationCode`, `homeTeamDefendingSide`, `periodDescriptor`, `timeInPeriod` | Haettu ja tarkistettu |
| | `/v1/gamecenter/{gameId}/boxscore` | Pelaajakohtaiset ottelutilastot ja peliaika, aloittanut maalivahti | Polku vahvistettu; kenttien nimet tarkistettava rakentaessa |
| | `/v1/roster/{joukkue}/current`, `/v1/standings/now`, `/v1/club-schedule-season/{joukkue}/now`, `/v1/gamecenter/{id}/landing`, `/right-rail`, `/v1/score/{pvm}`, `/v1/player/{id}/game-log/{kausi}/{tyyppi}` | Kokoonpanot, sarjataulukko, lepopäivät | Polut vahvistettu yhteisön referenssistä (Zmalski/NHL-API-Reference) |
| NHL stats REST | `https://api.nhle.com/stats/rest/en/team/summary`, `team/powerplay`, `team/penaltykill`, `skater/summary`, `goalie/summary` (`?cayenneExp=seasonId=20262027 and gameTypeId=2`) | Yli- ja alivoimaprosentit nostoihin, ristiintarkistus | **Muistinvarainen — tarkistettava** |
| The Odds API v4 | `/v4/sports/icehockey_nhl/odds` | Kertoimet | Dokumentaatio tarkistettu: laji `icehockey_nhl`; hinta = markkinat × alueet; `bookmakers`-parametri ohittaa alueet ja 10 välittäjää = 1 alue; `/events` on ilmainen; otsakkeet `x-requests-remaining`, `x-requests-used`, `x-requests-last` |
| | Alueet | `fi`: `veikkaus_fi`, `unibet_fi`, `leovegas_fi`. `eu`: mm. `pinnacle`, `betsson`, `coolbet`, `nordicbet`, `unibet_se`, `betfair_ex_eu`, `marathonbet`, `williamhill` | Tarkistettu välittäjälistasta |
| ESPN (epävirallinen) | `site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries` | Loukkaantumiset | **Ei varmennettu:** palautti tässä istunnossa HTTP 403. Toimivuus Actions-ajossa testattava; varatapa 6.6 |
| Daily Faceoff | Aloittavien maalivahtien sivu | Maalivahdin tila vahvistettu / todennäköinen | **Ei varmennettu.** HTML-sivu, hauras; varatapa 6.5 |
| MoneyPuck | Ladattavat laukausdatat | Oman xG-mallin vertailukohta rakennusvaiheessa (ei ajonaikainen riippuvuus) | Ei varmennettu; käyttöehdot tarkistettava |

*Suunnittelijan valinta:* **kaikki pelaaja- ja joukkuetilastot johdetaan ottelukohtaisesta välimuistista (boxscore + play-by-play)**, ei pelaajakohtaisista game-log-kutsuista. Näin tavallinen ajo tekee vain joitakin kymmeniä pyyntöjä (ohjelma, sarjataulukko, yön päättyneet ottelut × 2, pelaavien joukkueiden kokoonpanot, kertoimet, loukkaantumiset, maalivahdit).

### 5.1 Välimuistin sisältö

Yksi tiedosto per päättynyt ottelu (`gameState` on `OFF` tai `FINAL`), pakattu JSON, tavoitekoko alle 20 kt:

- ottelun perustiedot: id, päivä, koti/vieras, lopputulos, ratkaisutapa (REG/OT/SO)
- laukaukset (maalit, maalia kohti menneet, ohi menneet; ei blokattuja): erä, aika, joukkue, laukoja, maalivahti, normalisoidut koordinaatit, laukaustyyppi, tilannekoodi, lasketut piirteet ja xG
- pelaajarivit boxscoresta: peliaika, maalit, syötöt, laukaukset, ylivoimapisteet
- maalivahtirivit: aloittiko, torjunnat, päästetyt
- rangaistukset: erä, aika, joukkue, minuutit

xG tallennetaan laukaukselle laskettuna, mutta myös raakapiirteet säilytetään, jotta xG-mallin uudelleensovitus ei vaadi uutta latausta.

**Alkulataus (backfill):** kaksi edellistä kautta + kuluva kausi, noin 2 × 1 312 ottelua × 2 pyyntöä. Ajetaan kerran käsin käynnistettävällä workflow'lla, noin 3 pyyntöä/s → noin puoli tuntia. Skriptin on oltava jatkettava (ohittaa jo tallennetut ottelut).

---

## 6. Malli

Periaate: yksi läpinäkyvä ketju. Jokaiselle joukkueelle lasketaan tässä ottelussa odotettu maalimäärä varsinaisella peliajalla (λ), joka on perusvahvuus kerrottuna muutamalla nimetyllä kertoimella. Kertoimet näytetään käyttäjälle (`factors`). Kaikki vakiot ovat `config.py`:ssä.

### 6.1 xG-malli (laukauksen maalitodennäköisyys)

- **Malli:** logistinen regressio (scikit-learn), sovitetaan kahden edellisen kauden laukauksiin `train_xg.yml`-ajossa; kertoimet tallennetaan `model/xg_coefs.json`-tiedostoon. Ajonaikainen pisteytys on pelkkää numpy-laskentaa.
- **Havainnot:** blokkaamattomat laukaukset (`goal`, `shot-on-goal`, `missed-shot`). Pois: voittolaukauskilpailu, rangaistuslaukaukset, laukaukset tyhjään maaliin (käsitellään erikseen 6.9).
- **Koordinaattien normalisointi:** käännetään niin, että hyökkäysmaali on pisteessä (89, 0), käyttäen `homeTeamDefendingSide`- ja `eventOwnerTeamId`-kenttiä. Jos kenttä puuttuu, suunta päätellään joukkueen erän laukausten x-koordinaattien mediaanista.
- **Piirteet:** etäisyys maalista, kulma (itseisarvo), etäisyys × kulma, laukaustyyppi (luokka), pelitilanne `situationCode`:sta (tasakentällisin 5v5, muu tasakentällisin, ylivoima, alivoima), rebound (edellinen saman joukkueen laukaus alle 3 s sitten), rush (edellinen tapahtuma toisella alueella alle 4 s sitten), laukoja kotijoukkueessa.
- **Hyväksymiskriteeri:** testikauden log loss parempi kuin vakioennusteella ja AUC vähintään noin 0,75; joukkueiden kauden xG-summat korreloivat MoneyPuckin vastaaviin (silmämääräinen tarkistus rakennusvaiheessa).
- **Päivityspolku:** gradient boosting, laukausta edeltävä tapahtumaketju, laukojan ja maalivahdin kätisyys.

`situationCode` on nelinumeroinen (vierasmaalivahti, vieraskenttäpelaajat, kotikenttäpelaajat, kotimaalivahti), esim. `1551` = 5v5. Tulkinta varmistettava yksikkötestillä oikeasta datasta.

### 6.2 Joukkuevahvuudet

*Suunnittelijan valinta:* **yksi aikapainotettu Poisson-regressio** hoitaa samalla mekanismilla viime 10 ottelun, koko kauden ja edellisen kauden painotuksen sekä vastustajan tason, kotiedun ja lepoedun.

- **Rivit:** kaksi per ottelu (joukkue hyökkääjänä). Data: kuluva kausi + edellinen kausi.
- **Kohdemuuttuja:** `y = 0,6 × xG + 0,4 × maalit`, varsinainen peliaika, ilman tyhjän maalin tilanteita. xG ennustaa tulevaa paremmin kuin maalit, mutta maalit sisältävät viimeistely- ja maalivahtitaitoa, jota xG ei näe.
- **Selittäjät:** hyökkäävä joukkue (one-hot), puolustava joukkue (one-hot), kotiottelu, hyökkääjällä back-to-back (pelasi eilen), puolustajalla back-to-back.
- **Painot:** `w = 0,5 ^ (ikä / 40 vrk)`. Kausien välinen tauko lasketaan kiinteänä 60 vuorokautena, ja edellisen kauden otteluille lisäkerroin 0,7 (kokoonpanot muuttuvat kesällä). Seuraus: keskellä kautta viimeiset 10 ottelua kantavat karkeasti kolmanneksen painosta; kauden alussa edellinen kausi toimii priorina ja häipyy itsestään.
- **Sovitus:** `sklearn.linear_model.PoissonRegressor` L2-regularisoinnilla (vetää joukkueet kohti keskiarvoa, mikä hillitsee ylivarmuutta). Regularisoinnin voimakkuus ja puoliintumisaika viritetään backtestillä.
- **Tulos:** `λ_perus(A vastaan B) = exp(vakio + hyökkäys_A + puolustus_B + koti + b2b-termit)`.

Kotietu ja back-to-back-vaikutus tulevat datasta sovitettuina, ei käsin asetettuina. Odotettava suuruusluokka on muutaman prosentin luokkaa; jos sovitus antaa selvästi muuta, se on tarkistettava.

**Päivityspolku:** erilliset vahvuudet 5v5:lle, ylivoimalle ja alivoimalle + rangaistusmäärät; tilannekorjattu xG; matkustus ja aikavyöhykkeet; hierarkkinen Bayes-malli.

### 6.3 Ottelun λ

```
λ_A = λ_perus(A vs B) × m_maalivahti(B:n aloittaja) × m_hyökkäys(A:n kokoonpano)
                      × m_puolustus(B:n kokoonpano) × m_h2h(A,B)
```

Jokainen kerroin rajataan (ks. alla), ja kertoimien yhteistulo rajataan välille 0,80–1,25. Rajat ovat suoja virheellistä dataa vastaan.

### 6.4 Lepo ja back-to-back

Mallissa vain back-to-back (6.2). Muu väsymys (kolmas ottelu neljään päivään, pitkä vieraskiertue) näkyy v1:ssä vain nostoina.

### 6.5 Maalivahti

- **Taito:** `δ = (kohdattu xG − päästetyt maalit) / (kohdattu xG + K)`, K = 60 (kutistus kohti nollaa; vastaa karkeasti 25 ottelun dataa). Data: kuluva + edellinen kausi samoilla aikapainoilla.
- **Kerroin vastustajan maaliodotukseen:** `m = 1 − 0,6 × (δ_aloittaja − δ_joukkueen painotettu keskiarvo)`, rajattu 0,90–1,10. Vähennys joukkueen keskiarvosta, koska joukkueen puolustusvahvuus sisältää jo tavallisen maalivahtikaksikon vaikutuksen; kerroin 0,6 vastaa sitä, että vahvuuden kohdemuuttujasta 40 % on jo maaleja.
- **Kuka aloittaa:**
  1. Daily Faceoff, jos saatavilla → `confirmed` tai `likely`.
  2. Muuten päättely → `projected`: eniten viime 10 ottelusta aloittanut; jos joukkue pelasi eilen ja sama maalivahti aloitti, oletetaan toinen maalivahti.
  3. Kun tila on `projected`, kerroin lasketaan odotusarvona (esim. 70 % ykkösvahti, 30 % kakkonen), ei yhden maalivahdin mukaan.
- Käyttöliittymä näyttää tilan aina maalivahdin nimen vieressä.

### 6.6 Poissaolot, kaupat ja paluut

Yksi mekanismi kaikille kokoonpanomuutoksille. Ajatus: joukkueen vahvuus on mitattu otteluista, joissa pelaaja joko oli tai ei ollut mukana. Korjataan vain sen verran kuin tämän illan kokoonpano **poikkeaa** siitä, mitä vahvuusluku jo sisältää.

Jokaiselle pelaajalle i:

- `osuus_i` = hyökkäysosuus = `0,5 × (pelaajan xG / joukkueen xG) + 0,5 × (pelaajan pisteet / (joukkueen maalit × 2,7))`, laskettuna otteluista joissa hän pelasi (2,7 ≈ pisteitä per maali; tarkka arvo lasketaan datasta).
- `läsnäolo_i` = aikapainotettu osuus joukkueen otteluista, joissa hän pelasi (samat painot kuin 6.2).
- `odotus_i` = 1 jos pelaa tänään, 0 jos poissa, 0,5 jos epävarma.
- Korvaavan pelaajan taso ρ = 0,45 (korvaaja tuottaa 45 % puuttuvan pelaajan panoksesta).

```
m_hyökkäys  = 1 + Σ osuus_i × (1 − ρ) × (odotus_i − läsnäolo_i)
m_puolustus = 1 + Σ κ_i × peliaikaosuus_i × (läsnäolo_i − odotus_i)     κ = 0,5 puolustajille, 0,25 hyökkääjille
```

Molemmat rajataan välille 0,85–1,15.

Esimerkit: tähti, joka on pelannut kaikki ottelut ja on tänään poissa → täysi vähennys. Pelaaja, joka on ollut poissa jo kuukauden → vahvuus sisältää sen jo, vähennys lähes nolla. Pitkältä tauolta palaava → lisäys. Kaupalla hankittu pelaaja → läsnäolo uudessa joukkueessa 0, osuus lasketaan hänen vanhan joukkueensa luvuista suhteessa uuden joukkueen tasoon → lisäys; pois kaupattu → vähennys.

**Kokoonpanotiedon lähteet tärkeysjärjestyksessä:**

1. NHL:n `roster/{joukkue}/current` — kuka on kokoonpanossa; vertailu edelliseen ajoon (`state/rosters.json`) paljastaa kaupat ja siirrot. Tämä on luotettavin ja aina käytössä.
2. ESPN:n loukkaantumislista, jos saatavilla — tila ja kuvaus.
3. Päättely varatapana: pelaaja, joka pelasi vähintään 8 joukkueen 10 edellisestä ottelusta mutta puuttui kahdesta viimeisimmästä → `out`, lähde "päätelty".

Jokaisella merkinnällä on lähde näkyvissä. Jos ESPN ei toimi, lähteen tila kertoo sen, ja ennuste nojaa kohtiin 1 ja 3.

**Tämän illan kokoonpano (ennuste):** kokoonpanon pelaajat miinus poissaolijat; 18 kenttäpelaajaa valitaan viimeisimmän pelatun ottelun ja peliajan mukaan.

### 6.7 Keskinäiset kohtaamiset (pieni paino)

Viimeisten kolmen kauden kohtaamiset (enintään 8). `suhde = toteutunut kohdemuuttuja / mallin odottama` kyseisissä otteluissa; `m_h2h = 1 + (suhde − 1) × n / (n + 20)`, rajattu 0,97–1,03. Tarkoituksella pieni: keskinäisten otosten ennustearvo on heikko, mutta käyttäjä pyysi sen mukaan. Kohtaamiset näytetään käyttöliittymässä kokonaan riippumatta painosta.

### 6.8 Erät

- Liigan eräjakauma (maalien osuus erittäin) lasketaan datasta, ei kovakoodata.
- Joukkueelle: tehtyjen ja päästettyjen (kohdemuuttujan) osuus erittäin, kutistettuna kohti liigan jakaumaa 20 ottelun priorilla.
- `λ_A,erä = λ_A × normalisoitu(osuus_tehdyt_A,erä × osuus_päästetyt_B,erä / liigaosuus_erä)`.
- Samasta datasta nostoja varten: xG-osuus erittäin, voittoprosentti kun johtaa / häviää erätauolla, ensimmäisen maalin tehneenä.

### 6.9 Simulaatio

10 000 simulaatiota per ottelu, numpy-vektoreina, kiinteä siemenluku per ottelu (sama syöte → sama tulos).

1. **Epävarmuus:** jokaisessa simulaatiossa λ:t kerrotaan satunnaiskertoimilla: yhteinen "ottelun tempo" lognormaali(σ = 0,08) ja joukkuekohtainen lognormaali(σ = 0,08). Tämä tuo ylihajontaa (vastaa negatiivista binomijakaumaa) ja estää ylivarmat todennäköisyydet. σ viritetään kalibroinnin perusteella.
2. **Erät:** maalit per joukkue per erä ~ Poisson(λ_joukkue,erä).
3. **Tyhjän maalin maalit:** jos ero on 3. erän jälkeen 1 tai 2 maalia, johtava joukkue saa lisämaalin todennäköisyydellä, joka on estimoitu historiasta eron mukaan. Vaikuttaa maalimäärä- ja tasoitusvetoihin.
4. **Tasapelikorjaus:** riippumaton Poisson aliarvioi varsinaisen peliajan tasapelit (joukkueet pelaavat tasatilanteessa varman päälle). Tasapeliin päättyville simulaatioille annetaan paino κ, joka on estimoitu niin, että backtestissä tasapelien osuus vastaa toteutunutta. Kaikki tunnusluvut lasketaan painotettuina keskiarvoina.
5. **Jatkoaika ja voittolaukaukset:** `P(A voittaa) = 0,5 + 0,3 × (λ_A / (λ_A + λ_B) − 0,5)`. Voittaja saa yhden maalin lopputulokseen.
6. **Tulokset:** voittotodennäköisyys (2-tie ja 1X2), lopputulosmatriisi ja todennäköisin tulos, maalimäärän jakauma ja yli/alle halutuilla rajoilla, tasoitus ±1,5, erien tulokset, joukkueen maalimäärän jakauma (pelaajamallia varten).

Näytettävä "xG" on λ_A (varsinainen peliaika). Realistinen vaihteluväli on noin 2,2–4,0 per joukkue.

**Päivityspolku:** tilanneriippuvainen simulointi (johtoasema muuttaa tahtia), erikoistilanteiden simulointi, pudotuspelien jatkoaika.

### 6.10 Pelaajien piste- ja maalitodennäköisyydet

*Suunnittelijan valinta:* ei erillistä pelaajasimulaatiota. Pelaajan tulos johdetaan joukkueen simuloidusta maalijakaumasta, jolloin ottelun ympäristö (vastustaja, maalivahti, kotietu) välittyy automaattisesti.

- `q_i` = todennäköisyys, että joukkueen maalin tekee pelaaja i. Raaka-arvo: `0,5 × maaliosuus + 0,5 × xG-osuus` otteluissa, joissa hän pelasi.
- `π_i` = todennäköisyys, että pelaaja saa pisteen joukkueen maalista. Raaka-arvo: pisteet / joukkueen maalit niissä otteluissa.
- Kutistus: `(n × raaka + 15 × priori) / (n + 15)`, n = pelatut ottelut; priori = edellisen kauden arvo tai, jos sitä ei ole, pelipaikan ja peliajan mukainen keskiarvo.
- Peliaikakorjaus: kerrotaan suhteella (viime 5 ottelun peliaika / kauden peliaika), rajattu 0,8–1,25 (tarttuu ketjumuutoksiin).
- Normalisointi tämän illan kokoonpanon yli: Σq = 1 ja Σπ = joukkueen pisteet per maali. Kun tähti on poissa, muiden osuudet kasvavat automaattisesti.
- Ehdolla että joukkue tekee G maalia: pelaajan maalit ~ Binomi(G, q_i), pisteet ~ Binomi(G, π_i). `P(≥k) = Σ_G P(G) × P(Binomi ≥ k)` — lasketaan suoraan simulaation maalijakaumasta.

Tulos: jokaiselle pelaajalle P(≥1), P(≥2), P(≥3) maaleille ja pisteille. JSONiin 10 parasta per joukkue; frontend muodostaa top 5 -listat sekä ottelukohtaisesti että koko illalle.

**Päivityspolku:** ketju- ja ylivoimakokoonpanot (Daily Faceoff), jäällä olo maalien aikana, laukausmäärä-ennusteet, pelaajakertoimien vertailu.

### 6.11 Kertoimet ja ylikertoimet

**Haku.** *Suunnittelijan valinta:* yksi kutsu `bookmakers`-parametrilla ja täsmälleen 10 välittäjää (lasketaan yhdeksi alueeksi): `veikkaus_fi, unibet_fi, leovegas_fi, pinnacle, betsson, coolbet, nordicbet, unibet_se, betfair_ex_eu, marathonbet`. Markkinat `h2h,totals,spreads`, `oddsFormat=decimal`.

Budjetti: 3 markkinaa × 1 alue = 3 krediittiä per kutsu × 2 kutsua/vrk ≈ 186 krediittiä/kk (raja 500). Jos laskutus poikkeaakin dokumentoidusta, varatapa on `regions=eu,fi` ja `h2h,totals` (4 krediittiä per kutsu ≈ 248/kk). Suojat: kutsu jätetään väliin, jos seuraavan 24 tunnin aikana ei ole otteluita tai `x-requests-remaining` on alle 30; jäljellä oleva saldo näytetään tilasivulla. Lista välittäjistä on `config.py`:ssä.

**Laskenta** (per välittäjä ja markkina):

- Implisiittinen todennäköisyys `q_i = 1 / kerroin_i`; marginaali `Σq − 1`.
- Marginaaliton todennäköisyys `p_i = q_i / Σq` (suhteellinen menetelmä). Päivityspolku: potenssi- tai Shin-menetelmä.
- **Markkinataso** = Pinnaclen marginaaliton todennäköisyys; jos Pinnacle puuttuu, välittäjien marginaalittomien todennäköisyyksien mediaani.
- `edge = p_malli − p_markkina` (prosenttiyksikköä).
- `EV = p_malli × kerroin − 1` kullekin välittäjälle; näytetään paras kerroin ja Veikkauksen kerroin erikseen.
- **Ylikerroin**, kun EV ≥ 3 % ja edge ≥ 2 prosenttiyksikköä.
- **Varoitusleima** `suspicious`, kun |p_malli − p_markkina| > 10 prosenttiyksikköä: "Suuri ero markkinaan — todennäköisemmin mallin virhe tai puuttuva tieto kuin löytö." Näitä ei nosteta listan kärkeen.

**Markkinoiden vastaavuus** (rakentajan varmistettava ensimmäisestä oikeasta vastauksesta):

- `h2h` kahdella lopputuloksella = voittaja jatkoaika mukaan lukien → `home_win` / `away_win`. Jos välittäjän `h2h`:ssa on kolme lopputulosta (tasapeli mukana), verrataan 1X2-todennäköisyyksiin. Tunnistus lopputulosten määrästä.
- `totals`: raja luetaan vastauksesta; mallin P(yli) lasketaan samalle rajalle simulaatioista. Tasaluvuilla (esim. 6,0) palautus huomioidaan EV:ssä.
- `spreads`: tasoitus ±1,5.
- Joukkueiden nimet yhdistetään `teams.py`:n kartalla; yhdistämätön ottelu on tarkistusraportin virhe.

Kaikki haetut kertoimet tallennetaan `data/history/odds/`-lokiin tarkkuusseurantaa varten.

**Päivityspolku:** mallin ja markkinan sekoitettu todennäköisyys varovaisempaa EV:tä varten, osittainen Kelly-panos, kertoimien muutoksen seuranta.

---

## 7. Nostot (sääntömoottori)

Sääntöpohjainen, ei kielimallia ajossa. Jokainen sääntö on tietue: tunniste, ehto, vakavuus (1–3), suomenkielinen tekstipohja. Per ottelu näytetään enintään 6 nostoa vakavuusjärjestyksessä, enintään 3 per joukkue. Kaikissa vähimmäisotos 10 ottelua, ellei toisin mainita. Kynnykset ovat lähtöarvoja `config.py`:ssä.

| Tunniste | Ehto | Esimerkkiteksti |
|---|---|---|
| `period_strength` | Joukkueen xG-osuus erässä ≥ 55 % | "Carolina hallitsee 1. erää (xG-osuus 58 %)." |
| `period_weakness` | xG-osuus erässä ≤ 45 % | "2. erässä vastustajat luovat Carolinaa vastaan enemmän (xG-osuus 44 %)." |
| `period_swing` | Parhaan ja heikoimman erän xG-osuuden ero ≥ 10 prosenttiyksikköä | "Carolina on vahva 1. ja 3. erässä, mutta momentum kääntyy usein 2. erässä." |
| `period_matchup` | Joukkueen A vahva erä osuu B:n heikkoon erään | "Seattlen heikoin erä (3.) on Carolinan vahvin." |
| `third_period_collapse` | Johtanut 2. erän jälkeen ≥ 6 kertaa ja voittanut niistä alle 65 % | "Seattle on sulattanut johdon 3. erässä 4 kertaa 9:stä." |
| `comeback` | Voittanut ≥ 30 % otteluista, joissa oli tappiolla 2. erän jälkeen (väh. 6 ottelua) | "Carolina on noussut tappioasemasta voittoon 4 kertaa." |
| `first_goal` | Ensimmäisen maalin tekijänä voitto-% ≥ 80 tai ≤ 55 | "Kun Carolina tekee avausmaalin, se voittaa 85 % otteluista." |
| `pp_vs_pk` | Ylivoima liigan 8 parhaan joukossa ja vastustajan alivoima 8 heikoimman joukossa (tai päinvastoin) | "Carolinan ylivoima (26 %, 4.) kohtaa Seattlen alivoiman (74 %, 29.)." |
| `penalty_prone` | Vastustaja ottaa jäähyjä liigan 5 eniten ja oma ylivoima 10 parhaan joukossa | "Seattle ottaa paljon jäähyjä — Carolinan ylivoimalle tilaisuuksia." |
| `b2b` | Joukkue pelasi eilen | "Seattle pelaa toisena iltana peräkkäin, Carolina levänneenä." |
| `schedule_load` | Kolmas ottelu neljään päivään tai ≥ 4. peräkkäinen vierasottelu | "Seattlen neljäs peräkkäinen vierasottelu." |
| `streak` | Voitto- tai tappioputki ≥ 4, tai pisteputki ≥ 6 | "Carolina on voittanut 5 peräkkäin." |
| `form_vs_season` | Viime 10 ottelun xG-osuus poikkeaa kauden luvusta ≥ 5 prosenttiyksikköä | "Carolinan peli on parantunut: xG-osuus viime 10 ottelussa 58 % (kausi 52 %)." |
| `luck` | Viime 10 ottelun (maalit − xG) per ottelu itseisarvoltaan ≥ 0,6 | "Seattle on tehnyt selvästi enemmän maaleja kuin paikat edellyttävät — tulokset voivat tasaantua." |
| `goalie_edge` | Maalivahtien δ-ero vastaa ≥ 4 % maaliodotuksessa | "Maalivahtiero Carolinan eduksi." |
| `goalie_unconfirmed` | Jommankumman maalivahdin tila `projected` | "Seattlen aloittava maalivahti on arvio, ei vahvistettu." |
| `key_absence` | Poissaolijan vaikutus hyökkäykseen ≥ 2 % | "Seattlen ykkössentteri poissa: maaliodotus −3 %." |
| `return_or_trade` | Palaava tai hankittu pelaaja, vaikutus ≥ 2 % | "Uusi hankinta mukana ensimmäistä kertaa." |
| `vs_last_season` | Pisteprosentti muuttunut ≥ 10 prosenttiyksikköä viime kaudesta (väh. 15 ottelua) | "Carolina on selvästi viime kautta parempi (68 % vs. 55 %)." |
| `h2h_dominance` | Voittanut ≥ 5 viimeisestä 6 kohtaamisesta | "Carolina on voittanut 5 viimeisestä 6 kohtaamisesta (pieni otos)." |
| `home_road_split` | Koti- ja vieraspisteprosentin ero ≥ 20 prosenttiyksikköä | "Seattle on vieraissa selvästi heikompi." |
| `model_vs_market` | Varoitusleima `suspicious` | "Malli ja markkina ovat eri mieltä yli 10 prosenttiyksikköä — tarkista poissaolot." |

Tekstit kertovat aina luvun ja, kun otos on pieni, sen koon. Säännöt eivät muuta ennustetta; ne selittävät dataa.

---

## 8. Kalibrointi ja seuranta

Tarkoitus: käyttäjä näkee, kannattaako malliin luottaa.

**Tallennus.** Jokaisesta ajosta rivi per ottelu `predictions/{kausi}.jsonl`-lokiin: aikaleima, mallin versio, todennäköisyydet, λ:t, maalimääräodotus, top-pelaajien todennäköisyydet, markkinataso ja parhaat kertoimet, ylikerroinpoiminnat, `official`-lippu. Aamuajo kirjaa tulokset `results`-lokiin ja laskee mittarit uudelleen koko lokista (ei kumulatiivista tilaa, joka voisi mennä rikki).

**Mittarit** (`performance.json`), aina otoskoon kanssa:

| Mittari | Vertailu |
|---|---|
| Brier-pistemäärä ja log loss, voittaja | vs. "kotijoukkue aina liigan kotivoitto-%" ja vs. markkinataso |
| Osumatarkkuus (suosikki voitti) | vs. markkinan suosikki |
| Kalibrointitaulukko: ennusteet 5 prosenttiyksikön lokeroissa → toteutunut voitto-% | Diagonaali |
| Maalimäärä: keskivirhe ja yli/alle-osumat | vs. markkinan raja |
| Ylikerroinpoiminnat: lukumäärä, osuma-%, ROI tasapanoksin sillä kertoimella, joka poimintahetkellä oli tarjolla | Erikseen kaikki / vain ilman varoitusleimaa / vain Veikkaus |
| CLV: poimintahetken kerroin vs. viimeisin haettu Pinnaclen marginaaliton kerroin | > 0 on hyvä merkki jo pienellä otoksella |
| Pelaajat: Brier ennusteelle "vähintään 1 piste" | vs. pelaajan kauden perustaso |

Jaksot: 30 vrk ja koko kausi. Alle 100 ratkenneen ennusteen otoksella näkymässä lukee "Otos pieni — luvut eivät vielä kerro paljoa". Rehellinen odotus: hyvin tehty julkinen malli on tyypillisesti hieman markkinaa heikompi; jos malli näyttää olevan markkinaa selvästi parempi, epäile ensin virhettä (esim. tulevaisuuden tiedon vuotoa backtestissä).

**Backtest ennen käyttöönottoa:** malli ajetaan edellisen kauden läpi päivä kerrallaan vain kyseiseen päivään mennessä tunnetulla datalla. Tällä viritetään luvun 6 parametrit, ja tulos näytetään suorituskykysivulla erillään merkittynä "Backtest 2025–26" — sitä ei sekoiteta eläviin ennusteisiin. Backtestissä ei ole historiallisia kertoimia, joten ROI kertyy vain elävästä käytöstä.

---

## 9. Käyttöliittymä (mobiili)

Yksi staattinen sivu, hash-reititys, alanavigaatio neljällä välilehdellä. Visuaalinen suunnittelu tehdään rakennusvaiheessa UI Pro Max -taidolla; tässä määritellään sisältö ja järjestys. *Suunnittelijan valinnat:* tumma teema oletuksena, joukkuelogot NHL:n rajapinnan osoitteista, kaaviot inline-SVG:nä ilman kirjastoa, PWA-manifesti (lisäys kotinäytölle), ei service workeria v1:ssä (vanhentunut välimuisti olisi ristiriidassa tuoreusvaatimuksen kanssa).

**Yläpalkki joka näkymässä:** "Päivitetty 23.41 (12 min sitten)" + tilapiste (vihreä / keltainen / punainen). Punainen banneri, jos data on yli 14 h vanhaa tai jokin lähde on epäonnistunut.

**1. Ottelut (aloitus)**
```
Ensi yön ottelut · su 15.11.                    ● Päivitetty 23.41
┌──────────────────────────────────────────────┐
│ 02.00   SEA  36 %  ▓▓▓▓░░░░░░░  64 %  CAR    │
│         xG 2,6 – 3,4   ·  todennäk. 2–3      │
│         ⚑ SEA back-to-back  ·  ★ ylikerroin  │
└──────────────────────────────────────────────┘
```
Kortti per ottelu: kellonaika, todennäköisyyspalkki, xG, todennäköisin tulos, enintään kaksi merkkiä (tärkein nosto, ylikerroin, vahvistamaton maalivahti). Alla "Illan kuumimmat pelaajat": top 5 pisteille ja maaleille kaikista otteluista.

**2. Ottelunäkymä** (osiot järjestyksessä, vieritettävä)
1. Otsikko: joukkueet, aika, voittotodennäköisyydet isona, 1X2 pienempänä, varmuusleima.
2. xG ja tulos: xG-palkit, todennäköisin lopputulos, 5 todennäköisintä tulosta, maalimäärän jakauma ja yli/alle.
3. Mistä ennuste koostuu: `factors`-kertoimet vesiputouksena (kotietu, lepo, maalivahti, poissaolot, keskinäiset).
4. Nostot.
5. Erät: xG per erä kummallekin, erävoiton todennäköisyys.
6. Pelaajat: välilehdet Pisteet / Maalit ja 1+ / 2+ / 3+; top 5 koko ottelusta, laajennettavissa.
7. Maalivahdit ja poissaolot: tila- ja lähdeleimat, vaikutus prosentteina.
8. Vire: viime 10 ottelua (V/H/J-rivi, maalit, xG-osuus) rinnakkain.
9. Kausivertailu: tämä kausi vs. viime kausi.
10. Keskinäiset: viimeisimmät kohtaamiset tuloksineen ja xG:ineen.
11. Kertoimet: taulukko välittäjittäin, markkinataso, mallin luku, edge ja EV; ylikertoimet korostettu, varoitusleima näkyvissä.

**3. Ylikertoimet**
```
CAR voittaa · Coolbet 1,66        EV +6,6 %
Malli 64,2 %  ·  Markkina 61,5 %  ·  edge +2,7
Veikkaus 1,55 (EV −0,5 %)
```
Lista EV:n mukaan; suodattimet: markkina, vain Veikkaus, piilota varoitusleimalliset (oletuksena piilossa listan lopussa). Tyhjä tila: "Ei ylikertoimia juuri nyt" + kertoimien hakuaika. Alareunassa pysyvä huomautus: luvut ovat mallin arvioita, eivät takuita.

**4. Malli** (suorituskyky): Brier ja log loss vs. markkina, osumatarkkuus, kalibrointikäyrä, ylikerroinpoimintojen ROI ja CLV, viimeisimmät ratkenneet ennusteet (ennuste → tulos), backtest erikseen.

**5. Tila** (yläpalkin tilapisteestä): lähteet riveinä (tila, viimeisin onnistunut haku, huomautus), kerroinkrediittien saldo, tarkistusraportin ongelmien määrä, mallin versio.

Virhetilat: `latest.json` ei lataudu → "Dataa ei saatu ladattua" + uudelleenyritys; ei otteluita → seuraavan pelipäivän päivämäärä; tuntematon `schema_version` → selkeä virheilmoitus.

---

## 10. Tarkistusskripti

**Tarkoitus:** tekoälyavustaja lukee yhden lyhyen raportin eikä koko koodikantaa. Raportissa on **vain ongelmat**.

- **Ajo:** `python tools/review.py [--skip-tests] [--data-only]`. Ajetaan (a) jokaisessa pipeline-ajossa viennin jälkeen `--data-only`-tilassa ja (b) paikallisesti tai pull requestissa täytenä.
- **Syötteet:** lähdekoodi, `site/data/*.json`, `data/state/sources.json`, `data/history/*`, `data/cache/`.
- **Tuloste:** `reports/review.md` (kommitoidaan) ja poistumiskoodi: 0 = ei ongelmia, 1 = varoituksia, 2 = virheitä. Pipeline-ajossa virhetason dataongelma estää julkaisun (edellinen sivu jää voimaan). Ongelmien määrä viedään myös `latest.json`:n `review`-kenttään.

**Tarkistukset:**

| Ryhmä | Tarkistus | Taso |
|---|---|---|
| LINT | `ruff check` — vain löydökset | varoitus |
| TEST | `pytest -q` — vain epäonnistuneet testit, virheen ensimmäinen rivi | virhe |
| SITE | JSON vastaa sopimusta (vaaditut kentät, tyypit); `node --check site/app.js` jos node on saatavilla | virhe |
| PROB | `home_win + away_win = 1` ja 1X2-summa = 1 (toleranssi 0,001); tulosmatriisin summa = 1 (toleranssi 0,01); kaikki todennäköisyydet välillä 0–1 | virhe |
| PROB | Voittotodennäköisyys yli 0,80 tai alle 0,20; λ välin 1,5–5,0 ulkopuolella; kertoimien yhteistulo osuu rajaan | varoitus |
| PROB | Pelaajan P(≥1) ≥ P(≥2) ≥ P(≥3); joukkueen pelaajien maaliodotusten summa ≈ joukkueen λ (toleranssi 10 %) | virhe |
| ROSTER | Jokaisella ottelulla molemmilla joukkueilla ≥ 18 kenttäpelaajaa ja aloittava maalivahti; vähintään 5 pelaajaa top-listalla | virhe |
| SCHED | Ohjelman jokainen tuleva ottelu löytyy `latest.json`:sta | virhe |
| ODDS | Kertoimet haettu < 14 h sitten kun otteluita on tulossa; jokaiselle ottelulle ≥ 1 välittäjä; yhdistämättömiä joukkueita ei ole; marginaali välillä 0–15 %; krediittejä jäljellä < 60 | varoitus (yhdistämätön joukkue: virhe) |
| FRESH | `generated_at` < 1 h (pipeline-tilassa); jokaisen lähteen `fetched_at` kynnyksen sisällä; välimuistista ei puutu yli 24 h sitten päättyneitä otteluita | virhe / varoitus |
| SOURCE | Lähteen tila `failed` tai `stale` | varoitus |
| TRACK | Ennustelokin viimeisin rivi < 24 h; jokaisella yli 24 h sitten päättyneellä ottelulla tulos; mittarit laskettavissa | varoitus |
| XG | Viime 7 päivän laukausten xG-summa / maalit välillä 0,85–1,15 (xG-malli ei ole ajautunut) | varoitus |
| SECRET | API-avaimen muotoista merkkijonoa ei löydy repon tiedostoista eikä generoidusta datasta | virhe |

**Raportin muoto** (enintään 40 riviä; samanlaiset niputetaan):

```
# Tarkistusraportti 2026-11-15T21:44Z — 1 virhe, 2 varoitusta
[VIRHE] ROSTER pipeline/players.py — SEA: kokoonpanossa 16 kenttäpelaajaa (ottelu 2026020321) — vihje: tarkista roster-haun validointi
[VAROITUS] SOURCE injuries — ESPN HTTP 403, käytetään pääteltyä tietoa (viimeisin onnistunut 14.11. 15.20)
[VAROITUS] LINT pipeline/value.py:41 F841 käyttämätön muuttuja (+3 muuta samanlaista)
```

Jos ongelmia ei ole, raportissa on yksi rivi: `OK — 0 ongelmaa`. Jokaisella rivillä: taso, ryhmä, tiedosto (ja rivi jos tiedossa), mitä havaittiin lukuineen, lyhyt vihje. Ei onnistuneita tarkistuksia, ei koko lokeja.

**Käyttötapa:** korjauspyyntö avustajalle on "lue `reports/review.md` ja korjaa" — avustaja avaa vain raportissa nimetyt tiedostot.

---

## 11. Testaus

- Yksikkötestit kiinteillä esimerkkivastauksilla (`tests/fixtures/`: yksi oikea play-by-play, boxscore, ohjelma, kerroinvastaus) — testit eivät tee verkkokutsuja.
- Pakolliset testit: koordinaattien normalisointi, `situationCode`-tulkinta, marginaalin poisto ja EV tunnetuilla luvuilla, simulaation summat ja toistettavuus siemenluvulla, kokoonpanokertoimen esimerkit luvusta 6.6, binomilaskenta pelaajille, jokainen nostosääntö (laukeaa / ei laukea), JSON-sopimuksen validointi, lähteen epäonnistumisen sietäminen (valinnainen lähde palauttaa virheen → ajo onnistuu ja tila on `failed`).
- Koko pipeline ajettavissa paikallisesti fixtureilla ilman verkkoa ja ilman API-avainta (`--offline`).

---

## 12. Työnjako mallien kesken

Tavoite: kallis malli suunnittelee ja ratkaisee, halvat mallit kirjoittavat koodin rajatuissa tehtävissä aliagentteina.

| Rooli | Malli | Tehtävät |
|---|---|---|
| Suunnittelu ja ohjaus | Fable 5.2 | Tämä dokumentti, toteutussuunnitelma tehtäviksi pilkottuna, moduulien rajapinnat, parametrien viritys backtestin tuloksista, päätökset kun tarkistusraportti tai backtest paljastaa mallinnusongelman |
| Vaativa koodi | Sonnet | `xg.py`, `ratings.py`, `adjust.py`, `simulate.py`, `players.py`, `tracking.py`, backtest, frontend (UI Pro Max -taidolla) |
| Suoraviivainen koodi | Haiku | `sources/*` (haku + validointi), `teams.py`-kartta, `cache.py`, `value.py`, `export.py`, nostosääntöjen tekstit ja testit, `tools/review.py`, workflow-tiedostot, fixtureiden keruu |

Pelisäännöt:

1. Jokainen tehtävä annetaan aliagentille omana rajattuna kokonaisuutenaan: tämän dokumentin asiaankuuluva luku, moduulin rajapinta (funktioiden nimet, syötteet, palautusmuodot) ja hyväksymistestit. Aliagentti ei lue koko repoa.
2. Moduulien väliset rajapinnat ja JSON-sopimus lukitaan ennen koodausta, jotta tehtävät voidaan tehdä rinnakkain.
3. Jokaisen tehtävän jälkeen ajetaan `tools/review.py`; ohjaava malli lukee vain raportin.
4. Mallinnuspäätöksiä (luku 6) ei delegoida: jos toteuttaja törmää epäselvyyteen, se palauttaa kysymyksen eikä keksi ratkaisua.
5. Rakennusjärjestys: lähteet ja välimuisti → backfill → xG → vahvuudet ja simulaatio → backtest ja viritys → pelaajat → kertoimet → nostot → vienti → frontend → workflow't. Tarkistusskripti rakennetaan heti lähteiden jälkeen, jotta se on käytössä koko ajan.

---

## 13. Rakentajalle jäävät asiat

Tarkistettava oikeaa dataa vasten ennen kuin niiden varaan rakennetaan:

1. Boxscoren kenttien nimet (peliaika, aloittanut maalivahti) ja stats REST -polut (muistinvaraisia).
2. ESPN:n loukkaantumisrajapinnan toimivuus GitHub Actionsista (403 tässä istunnossa); tarvittaessa toinen lähde tai pelkkä päättely.
3. Daily Faceoffin sivun rakenne ja se, onko haku käyttöehtojen puitteissa kohtuullista henkilökohtaisessa käytössä; muuten v1 julkaistaan pelkällä päättelyllä.
4. The Odds API: `bookmakers`-parametrin todellinen krediittihinta, Veikkauksen `h2h`-markkinan muoto (2 vai 3 lopputulosta), sisältävätkö `totals` ja `spreads` jatkoajan kullakin välittäjällä, ja mitä markkinoita Veikkaus ylipäätään tarjoaa NHL:ään.
5. `situationCode`-tulkinta ja koordinaattien suunta oikeasta ottelusta.

Päätettävä toteutussuunnitelmassa:

6. Kaikkien luvun 6 lähtöarvojen viritys backtestillä (puoliintumisaika, regularisointi, xG/maali-painotus, σ, κ, ρ, kutistusvakiot) ja nostojen kynnysten tarkistus niin, että nostoja syntyy sopivasti (ei jokaiseen otteluun kuutta).
7. Moduulien tarkat funktiorajapinnat ja JSON-skeeman lopullinen muoto (esim. JSON Schema -tiedostona, jota sekä vienti että tarkistusskripti käyttävät).
8. Kauden vaihteen käsittely (kauden tunniste, tyhjä ohjelma kesällä, workflow'n uudelleenkäynnistys) ja pudotuspelit (ei voittolaukauksia).
9. Sivun hakukonenäkyvyys (`noindex`) ja repon nimi.
10. Visuaalinen suunnittelu UI Pro Max -taidolla luvun 9 sisältörungon päälle.

## 14. Avoimet kysymykset käyttäjälle

1. **Julkinen repo:** ilmainen GitHub Pages edellyttää julkista repoa, eli koodi, ennusteet ja sivu ovat periaatteessa kenen tahansa löydettävissä (API-avain pysyy salassa). Sopiiko, vai halutaanko yksityinen repo (vaatii maksullisen GitHub-tilin)?
2. **Välittäjät:** mitkä kymmenen välittäjää vertailuun? Ehdotus luvussa 6.11. Käytätkö itse vain Veikkausta vai myös muita — tämä ratkaisee, mitä "ylikertoimet"-näkymä oletuksena korostaa.
3. **Markkinat:** riittävätkö voittaja ja maalimäärä, vai halutaanko myös tasoitus (±1,5) ja 1X2 heti alussa?
4. **Kellonajat:** katsotko sivua tyypillisesti illalla ennen yön pelejä? Ajoajat (noin 8.45, 17.15 ja 23.40) on valittu sen mukaan.
5. **Panossuositus:** halutaanko myöhemmin näkyviin laskennallinen panoskoko (osittainen Kelly), vai pelkkä EV? **Ratkaistu (päätös 35): osittainen Kelly näytetään.**

## 15. Täsmennykset rakentajatarkistuksen jälkeen (suunnittelijan valinta)

1. **Backtestin vuotojen esto:** alkulataus kattaa myös kauden 2023-24. Backtestissä 2025-26 xG-malli opetetaan vain kausilla 2023-24 ja 2024-25, ja joukkuevahvuuksien priori tulee 2024-25:stä. Tuotannon xG-malli opetetaan uudelleen kahdella viimeisimmällä päättyneellä kaudella.
2. **Kokoonpanot backtestissä:** historiallisia loukkaantumis- ja maalivahtitietoja ei ole. Backtest ajetaan siksi kahdesti: (a) ilman kokoonpano- ja maalivahtisäätöjä ja (b) säädöt rekonstruoituna boxscoresta, jolloin tiedetään kuka oikeasti pelasi ja kuka oli maalissa ("täydellinen tieto" -yläraja). ρ, maalivahtivakio ja 0,6-paino viritetään versiolla (b), ja tuotannossa tarkkuutta seurataan oikean ennusteen perusteella.
3. **Julkaisuehto:** ottelumallin Brier- ja log loss -lukujen on backtestissä oltava parempia kuin perustasolla "liigan kotivoittoprosentti". Jos ehto ei täyty, malli yksinkertaistetaan (ensin xG-painotus pois, sitten erätason mallinnus pois) ja testi ajetaan uudestaan. Markkinaa vastaan ei aseteta tavoitetta, mutta CLV ja ROI raportoidaan. xG-mallin AUC-raja ~0,75 on tavoite, ei este: 0,70 tai enemmän riittää.
4. **Työnjako:** luvun 12 mallijako on ohjeellinen. Rakentaja saa ratkaista mallinnuksen yksityiskohdat itse ja kirjaa ne tähän dokumenttiin. Fablelle palataan vain, jos backtestin julkaisuehto ei täyty kahdella yrityksellä.
5. **MVP ensin:** järjestys on 1) data, välimuisti, xG, joukkuemalli, simulointi, ottelulista ja ottelusivu sekä ennusteloki (seuranta alkaa heti), 2) kertoimet ja ylikertoimet, 3) pelaajatodennäköisyydet, 4) nostot, H2H ja suorituskykysivu. Viikonlopun päiväpelejä varten lisätään ajo noin 2 h ennen ensimmäistä alkavaa peliä, jos se osuu ajastettujen ajojen ulkopuolelle.

## 16. Käyttäjän päätökset (2026-11-15)

- **Repo:** julkinen, ilmainen GitHub Pages.
- **Välittäjät:** kaikki luvun 6.11 kymmenen välittäjää. Ylikertoimet-näkymä näyttää kaikki ja korostaa parhaan kertoimen. Vertailutasona on Pinnacle.
- **Markkinat heti alusta:** voittaja (h2h), yli/alle (totals), tasoitus ±1,5 (spreads) ja 1X2 varsinaiselta peliajalta. Rakentajan tarkistettava, tarjoaako The Odds API NHL:lle 1X2:n (`h2h_3_way`) perusmarkkinana. Jos se on saatavilla vain kalliina tapahtumakohtaisena hakuna, 1X2 jätetään pois ja käyttäjälle kerrotaan. Budjetti: 4 markkinaa × 2 hakua/vrk ≈ 248 krediittiä/kk, mikä mahtuu 500:aan.
- **Ajot:** aamu-, ilta- ja myöhäisilta-ajo (luvun 6 ajastus sellaisenaan). Kertoimet haetaan illalla ja myöhäisillalla.
- **Panoskoko (Kelly):** ei ensimmäisessä versiossa, näytetään vain EV (suunnittelijan valinta, mahdollinen myöhempi lisäys).

## 17. Toteutussuunnitelman päätökset

Nämä päätökset täydentävät tai poikkeavat luvusta 15.4 (rakentaja saa ratkaista ne itse). Ne on kirjattu toteutuksen aikana.

1. **xG ei tallennu välimuistitiedostoihin.** Välimuistitiedostot sisältävät vain raakoja laukausominaisuuksia eikä niitä kirjoiteta uudelleen. xG lasketaan latauksen yhteydessä tiedostosta `model/xg_coefs.json`. Luku 5.1 tallentaisi xG:n, mikä on ristiriidassa muuttumattomien tiedostojen ja uudelleenkoulutuksen kanssa.
2. **`official` johdetaan, ei tallenneta.** Ottelun virallinen ennuste on viimeinen lokirivi, jonka `ts < start_utc`.
3. **Alkaneet ottelut näytetään jäädytettyinä** tiedostosta `data/state/last_games.json` (edellisen ajon viedyt ottelo-objektit). Niitä ei lasketa uudelleen.
4. **Lähteen tilan arvot ovat `ok | stale | failed | unavailable`.** `unavailable` tarkoittaa valinnaista lähdettä, joka ei tässä vaiheessa syötä mallia eikä sitä saatu haettua (ESPN:n loukkaantumiset). Se ei koskaan tee bannerista punaista.
5. **Viritetyt parametrit** ovat tiedostossa `model/params.json` (versioidaan). Ne korvaavat `config.py`:n oletukset. Tiedosto sisältää myös julkaisuehdon tuloksen. `pipeline.run` kieltäytyy ajamasta verkossa, ellei `gate.passed` ole tosi.
6. **Virityskausi on 2024-25 ja julkaisuehdon kausi 2025-26.** Parametrit jäädytetään ennen julkaisuehdon kautta, jotta ehto on otoksen ulkopuolinen.
7. **`train_xg.yml` on vain manuaalinen.** Opetuskaudet eivät muutu kesken kauden.
8. **Maalirivit välimuistissa sisältävät syöttäjien tunnisteet** (`a1`, `a2`). Näin myöhemmät vaiheet voivat johtaa ylivoimapisteet ilman uutta alkulatausta.
9. **Maalivahtisäätö kuuluu MVP:hen, aloittaja päätellään.** Ilman vahvistettua aloittajalähdettä jokainen maalivahtitila on `projected` ("Arvio") ja kerroin on kahden todennäköisen aloittajan odotusarvo. `confirmed` ja `likely` pysyvät sopimuksessa myöhempää lähdettä varten. Backtest ajetaan kolmella maalivahtitilalla: ei maalivahtisäätöä (15.2 a), todellinen boxscore-aloittaja (15.2 b, jolla maalivahtivakio viritetään) ja projisoitu (tuotannon tila). Julkaisuehto arvioidaan projisoidulla tilalla.
10. **`draw_kappa` sovitetaan kausille 2025-26 ja käynnissä olevalle kaudelle.** κ sovitetaan minimoimalla säännöllisen peliajan 1X2-log loss näiden kausien otteluissa. Syy: kausi 2025-26 oli tasapelirikas (tasapeliosuus 0,2485, kun aiemmat kaudet ovat noin 0,21). `tune()` käyttää samaa sovitusta. `MODEL_VERSION` on `1.1.0`.
11. **1X2-kertoimia ei haeta.** Lähde ei tarjoa `h2h_3_way`-markkinaa perushaussa. Tapahtumakohtaisena hakuna se veisi noin 420 krediittiä kuukaudessa lisää, mikä ylittää 500:n rajan. Jos jokin välittäjä palauttaa kolmen lopputuloksen `h2h`-markkinan (tasapeli mukana), sitä käytetään 1X2-kertoimena.
12. **Kertoimia haetaan vain `--odds`-ajoissa.** Kertoimet haetaan kello 15.17 ja 21.37 UTC sekä manuaaliajoissa. Muut ajot käyttävät `data/state/odds.json`-tiedostoa, jos se on alle 36 tuntia vanha. Hinnat arvioidaan jokaisessa ajossa uudelleen nykyistä mallia vasten.
13. **Vanhenemisraja on 16 tuntia, ei 14 tuntia.** Viikonloppuajo kello 12.17 UTC on 14 tuntia 40 minuuttia viimeisimmän haun jälkeen, joten 14 tunnin raja merkitsisi kertoimet vanhentuneiksi liian usein.
14. **Betfair Exchange -hinnoista vähennetään 5 %:n provisio voitoista ennen EV-laskentaa.** Jos marginaali (vig) on alle −1 % tai yli 15 %, kyseisen välittäjän markkina jätetään pois. Pörssin marginaali voi olla hieman nollan alapuolella.
15. **Arvopoiminta tehdään parhaalla hinnalla.** Jokaisesta poiminnasta näkyvät myös Veikkauksen oma hinta, EV ja se, täyttyykö ehto Veikkauksen hinnalla (`veikkaus.value`). Tämä ohjaa Ylikertoimet-näkymän suodatinta "Vain Veikkaus".
16. **Tasoitukset ja viivat rajataan.** Mallilla arvioidaan vain tasoitukset ±1,5 ja yli/alle-viivat, joiden viiva on kokonais- tai puolikasluku. Muut viivat näkyvät välittäjätaulukossa, mutta niille ei lasketa mallin todennäköisyyttä.
17. **Sulkeva kerroin (CLV-laskentaa varten) on Pinnacle-viite.** Se on viimeisen kertoimihistoriarivin ennen pelin alkua marginaaliton viitetodennäköisyys. Jos Pinnacle puuttuu, käytetään mediaania. Arvo johdetaan historiasta eikä tallenneta erikseen, samoin kuin virallinen ennuste.
18. **Varahaku käyttää alueita `eu,fi`.** Kun varahaku (luku 6.11) vaihtaa `bookmakers`-parametrin `regions=eu,fi`-parametriin, markkinat ovat samat kolme ja haun hinta on 6 krediittiä.
19. **Pelaajatasot ovat 60 minuutin tasoja, eivät osuuksia.** Pelaajalle lasketaan maalit/60 min kertoimesta `ĝ = xg_blend·ixG + (1 − xg_blend)·maalit` (luvun 6.10 0,5/0,5-painotus) ja pisteet/60 min kertoimesta `ĝ + syötöt`. Tasot painotetaan `ratings.weights`-painoilla (pelaajakohtainen puoliintumisaika) ja kutistetaan kohti hyökkääjien tai puolustajien keskiarvoa priorilla, jonka paino vastaa `prior_toi_h` tuntia jääaikaa (5 h ≈ 15 peliä). Edellinen kausi tulee mukaan painojen kautta (×0,7, 60 päivän väli), joten erillistä priortaulukkoa ei ole. Tasot seuraavat pelaajan tunnistetta, joten vaihdettu pelaaja säilyttää omat tasonsa uudessa joukkueessa.
20. **Ottelun kokoonpano on arvio.** Joukkueen kokoonpano on ne kenttäpelaajat, jotka pukeutuivat joukkueen viimeisimpään välimuistissa olevaan otteluun, vähennettynä ne, jotka pukeutuivat toiseen joukkueeseen sen jälkeen. `toi_proj` on pelaajan viimeisten 5 välimuistissa olevan pelin keskimääräinen jääaika (mistä joukkueesta tahansa). Tila on aina `projected` ("Arvio"). Luvun 6.6 kokoonpano- ja ESPN-lähteitä ei käytetä, koska kokoonpanohakua ei ole ja ESPN on `unavailable`. Uusi hankinta ilmestyy kokoonpanoon ensimmäisen pelinsä jälkeen uudessa joukkueessa.
21. **Hyvitettävät maalit.** Hyvitettäviä maaleja ovat säännöajan maalit tyhjän maalin maalit mukaan lukien, sekä jatkoajan voittomaali todennäköisyydellä `ot_goal_share`. Tämä on liigan osuus niistä jatkoaika- ja rangaistuslaukauspeleistä, jotka ratkesivat jatkoajalla, ja se lasketaan välimuistista. Rangaistuslaukauskilpailun maalit eivät kuulu kenellekään. Pelaajien maalit saadaan ohentamalla tätä jakaumaa.
22. **Kokoonpanon ulkopuolinen osuus.** Osuus `u` on niiden pelaajien osuus joukkueen maaleista, jotka pukeutuivat mutta eivät kuuluneet ennustettuun kokoonpanoon. Se mitataan virityskaudella ja tallennetaan tiedostoon `model/player_params.json`. Kokoonpanon odotettujen maalien summa on `(1 − u)` kertaa joukkueen hyvitettävien maalien odotusarvo.
23. **`game.players` on objekti, ei luvun 4.5 tasainen lista.** Muoto on `{lineup, home, away}`, ja joukkuekohtaiset yhteenvedot mahdollistavat summien tarkistuksen tarkistusskriptissä. Joukkueen `top` sisältää ne 10 pelaajaa, joilla on suurin P(vähintään 1 piste), yhdistettynä niihin 10 pelaajaan, joilla on suurin P(vähintään 1 maali). Jokaisella pelaajalla on `odds: null`, varattuna pelaajapropseille.
24. **Pelaajien julkaisuehto.** Tiedosto `model/player_params.json` saa kentän `gate.passed` arvon tosi vain, kun kauden 2025-26 pelaajabacktest voittaa lähtötason sekä Brier- että log loss -mittarissa molemmille todennäköisyyksille, P(vähintään 1 piste) ja P(vähintään 1 maali). Verkkoajot täyttävät `players`-kentän vain, kun ehto on täyttynyt. Offline-ajot täyttävät sen aina.
25. **Luvun 6.6 kokoonpano- ja loukkaantumiskerroin lykätään vaiheeseen 4.** Ilman pelaaja- tai loukkaantumislähdettä ainoa poissaolosignaali on "ei pelannut viimeisessä pelissä". Joukkueluvun 40 päivän puoliintumisaika sisältää sen jo osittain. ρ:n viritys vaatii backtest-variantin 15.2(b) kokoonpanoilla, ja se on erillinen porttikokeilu. Vaiheen 3 pelaajakohtaiset kontribuutioasteet tekevät lisäyksestä myöhemmin halvan. `factors` ja `lineup_changes` pysyvät ennallaan.
26. **Tasapelin valvonta** (luku 6.9, kohta 4). Mukana ovat viimeisimmät 300 nykykauden peliä, joilla on virallinen ennuste nykyisellä `MODEL_VERSION`-versiolla ja tulos. `gap = mallin keskimääräinen reg_draw − toteutunut säännöajan tasapelin osuus`. Tarkistus varoittaa, kun `n ≥ 100` ja `|gap| > 0,02`. Tilasivu näyttää arvon. Tässä vaiheessa ei tehdä uudelleensovitusta.
27. **Ei EV/PP-jakoa.** Välimuistin luistelijarivit sisältävät vain kokonaisjääajan, ei ylivoimaaikaa. Ylivoiman maaleja per 60 min ei siis voi laskea. Pelkkä laukausten `strength`-tieto antaisi ylivoimamaaleja ilman ylivoimaaikaa.
28. **Pelaajaloki on tiivis ja tulokset johdetaan.** `data/history/players/{season}.jsonl` sisältää yhden rivin jokaisesta tulevasta ottelusta ajokerrasta kohden, ja kaikki ennustetut pelaajat ovat taulukoina. Tulokset tulevat muuttumattomasta välimuistista (pelaajien tilastorivit) ja liitetään samoin kuin `official`, joten erillistä tuloslokia ei tehdä. `MODEL_VERSION` pysyy `1.1.0`:na, koska joukkuemalli ei muutu. Pelaajarivit kantavat `PLAYER_MODEL_VERSION = "1.0.0"`.
29. **Ylivoimatilaisuudet ja ylivoimamaalit ovat likiarvoja.** Ylivoimatilaisuuksia ovat vastustajan 2-, 4- ja 5-minuutin rangaistukset. Samanaikaisia rangaistuksia ei nettouteta. Ylivoimamaali on laukaus, jonka `strength == "pp"`. Myös tyhjän maalin 6v5-maalit voidaan laskea ylivoimaksi, joten luvut ovat likiarvoja.
30. **Keskinäiset kohtaamiset haetaan kahdelta välimuistissa olevalta kaudelta** (nykyinen ja edellinen), ei kolmelta. Mukana on enintään 8 viimeisintä ottelua sekä runkosarjasta että pudotuspeleistä.
31. **Nostoa `goalie_unconfirmed` ei toteuteta.** Kaikki maalivahdit ovat arvioita (päätös 9), joten nosto näkyisi jokaisessa ottelussa. Muut nostoihin liittyvät lisäykset: nostojen tekstit välttävät taivutettuja numeroita, ja koti/vieras-ero vaatii vähintään 5 ottelua kummaltakin puolelta.
32. **Suorituskykysivun perustaso on kauden ratkenneiden otteluiden kotivoitto-%.** Tasapanosten ROI lasketaan niin, että push kuuluu otokseen ja sen tuotto on 0. Betfairin 5 %:n provisio vähennetään voitoista. Veikkaus-ryhmä käyttää Veikkauksen hintaa, ja siihen kuuluvat vain poiminnat, joissa ehto täyttyy Veikkauksen hinnalla.
33. **Kokoonpano- ja H2H-kertoimet testattiin julkaisuehdolla.** ρ viritettiin kaudella 2024-25 toteutuneilla kokoonpanoilla (ρ = 0,30). Kaudella 2025-26 ennustetuilla kokoonpanoilla tulokset olivat seuraavat (log loss voittajasta / log loss 1X2):
    - perustaso 0,67905 / 1,06281
    - kokoonpano 0,68008 / 1,06188
    - H2H 0,67917 / 1,06203

    Kumpikaan muunnos ei voittanut perustasoa molemmilla mittareilla. Siksi `use_lineup` ja `use_h2h` ovat false, λ ei muutu ja `MODEL_VERSION` pysyy `1.1.0`:na. Poissaolot ja kaupat näytetään silti vaikutuksineen (ESPN ja pöytäkirjoista päätelty tieto). ESPN:n mukaan poissa olevat poistetaan pelaajaennusteista. Peliaikaosuus on jääaika / (5 × 60 min). Portti ajetaan uudelleen komennolla `python -m pipeline.lineup_backtest --tune-season 20242025 --gate-season 20252026`. ESPN:n poissaolijoiden poisto pelaajaennusteista muuttaa pelaajaennusteita, mutta PLAYER_MODEL_VERSION pysyy 1.0.0:na, joten Malli-sivun pelaaja-Brier sekoittaa muutosta edeltäviä ja sen jälkeisiä ennusteita.

34. **Maalimäärän yli/alle-osumat ja CLV nettokertoimella.** Päälinja on sulkevan kerroinrivin (`tracking.official` kerroinlokista) `ref.totals`-linja, jonka yli-todennäköisyys on lähimpänä 0,5:tä; tasatilanteessa valitaan pienempi linja. Mallin veto on yli, jos `total_mean` > linja, muuten alle (ei erillistä yli-todennäköisyyttä, ennustelokin muoto ei muutu). Markkinan veto on yli, jos marginaalittoman yli-todennäköisyyden arvo on > 0,5. Ratkaisu tehdään lopullisilla kokonaismaaleilla (koti + vieras, jatkoaika ja voittolaukaukset mukaan lukien); täsmälleen linjan osunut ottelu (push) jätetään pois. Jaksoon tallennetaan `totals`: `n`, `line_mean`, `model_hits`, `model_hit_rate`, `market_hits`, `market_hit_rate` (osuma-% on null, kun n = 0). CLV lasketaan Betfairin provision jälkeisellä nettokertoimella (`value.effective_price`), kuten ROI.
35. **Osittainen Kelly -panossuositus ylikertoimille.** Jokaiselle ylikerroinvedolle (`picks[]`) ja sen `veikkaus`-osiolle lasketaan `stake` (osuus kassasta, 0–0,03 tai null). Panoksen todennäköisyys kutistetaan markkinaa kohti, koska mallin ei ole osoitettu olevan markkinaa parempi: `p = KELLY_BLEND·p_model + (1 − KELLY_BLEND)·p_market`, `KELLY_BLEND = 0,5`. Nettokerroin `b = effective_price(book, price) − 1` (Betfairin provisio mukana), täysi Kelly `f* = (b·p − (1 − p)) / b` ja panos `min(KELLY_FRACTION·f*, KELLY_CAP)`, `KELLY_FRACTION = 0,25`, `KELLY_CAP = 0,03`. `stake` on null, kun f* ≤ 0, kun jompikumpi todennäköisyys puuttuu tai kun veto on `suspicious`-leimattu; pyöristys 4 desimaaliin. Vakiot ovat `pipeline/config.py`:ssä. Ylikerroinnäkymä näyttää panoksen prosentteina ("Panos: 1,2 % kassasta") ja, kun käyttäjä on syöttänyt kassan (tallennus selaimen localStorageen), myös euroina. Ei sijoitusneuvo.
36. **Pudotuspelit (`game_type` 3).** Pudotuspeleissä ei ole voittolaukauskilpailua: varsinaisen peliajan tasapeli ratkeaa kokonaan jatkoajalla. Kotijoukkueen jatkoaikavoiton osuus lasketaan samalla mallilla kuin runkosarjassa (`ot_slope`, luku 6.9 kohta 5), joten `home_win + away_win = 1` ja 1X2-todennäköisyydet ovat samat kuin vastaavassa runkosarjaottelussa. Ero on hyvitettävissä maaleissa: pudotuspeleissä jatkoajan voittomaali hyvitetään pelaajalle todennäköisyydellä 1 eikä `ot_goal_share`-osuudella (päätös 21). `predict_game` välittää ottelun `game_type`-arvon simulaatiolle. `ot_goal_share` lasketaan nyt vain runkosarjan otteluista, koska pudotuspelien jatkoaikapelit ratkeavat aina jatkoajalla ja nostaisivat osuutta (välimuistin kausilla 2025-26 ja 2026-27 0,660 → 0,641). Muutos vaikuttaa vain pelaajaennusteisiin; joukkueennusteet pysyvät bitilleen samoina, joten `MODEL_VERSION` ei muutu. Joukkuevahvuudet ja pelaajatasot opetetaan edelleen vain runkosarjasta (`TRAIN_GAME_TYPES`), joten pudotuspelien tulokset eivät päivitä niitä. Maalivahtien aloittaja-arvio ja kokoonpanoarvio käyttävät kaikkia välimuistin otteluita, myös pudotuspelejä. Tasapelipaino κ on sama kuin runkosarjassa.
37. **Kesätauko ja esikausi.** Esikauden otteluita (`game_type` 1) ei mallinneta eikä tallenneta välimuistiin. Kun haetuilla viikoilla ei ole tulevia runkosarja- tai pudotuspeliotteluita, `latest.json` julkaistaan tyhjällä `games`-listalla. `next_game_date` on seuraava haettujen viikkojen ottelupäivä; jos sellaista ei ole, se on otteluohjelman `regularSeasonStartDate`, kun se on tulevaisuudessa, ja muuten null. Kausi on tällöin välimuistin uusin kausi. Tarkistusskripti ei anna tyhjästä ohjelmasta virheitä, mutta FRESH-tarkistus valvoo edelleen `generated_at`-aikaleimaa.
38. **Kauden vaihde.** Kausi otetaan valittujen otteluiden `season`-kentästä, joten uuden kauden ensimmäisenä päivänä edellinen kausi toimii priorina painojen kautta. `season_compare.now` on null ja `last` on edellisen kauden luku. Vire (viime 10 runkosarjaottelua) ulottuu edelliselle kaudelle. Tilastopohjaiset nostot vaativat nykykauden otoksen, joten ne ovat kauden alussa hiljaa; keskinäisiin kohtaamisiin perustuva nosto voi näkyä (päätös 30). `performance.json` kattaa vain nykykauden, joten Malli-sivu on kauden alussa tyhjä. Vire, kausivertailu ja nostojen joukkuetilastot käyttävät vain runkosarjan otteluita myös pudotuspelien aikana; keskinäiset kohtaamiset sisältävät pudotuspelit.
