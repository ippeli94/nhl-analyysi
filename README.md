# NHL-otteluanalyysi

Laskee NHL-otteluille todennäköisyydet (voitto, tasatilanne, tarkat tulokset, erät) ja näyttää ne mobiilisivulla. Malli käyttää laukausdataa (xG), joukkuevahvuuksia, lepoa ja maalivahtisäätöä. Ennusteet ovat tilastollisia arvioita, eivät vedonlyöntineuvoja.

## Paikallinen käyttö

Vaatimukset: Python 3.12 ja Node 22 (vain sivun testeihin).

```bash
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pipeline.run --offline tests/fixtures   # ajo kiinteillä esimerkkivastauksilla
python tools/review.py                            # tarkistusraportti
python -m pytest
node --test site/tests/format.test.js
```

## Ajastus

GitHub Actions ajaa putkea kolme kertaa vuorokaudessa. Ajat ovat UTC:ssä, suomalaiset ajat talviaikaa:

| UTC   | Suomen aika | Sisältö |
|-------|-------------|---------|
| 06:43 | 08.43       | Yön tulokset, tarkkuusmittarit, ensimmäiset ennusteet |
| 15:17 | 17.17       | Loukkaantumiset, kokoonpanot, ennusteet, kertoimet |
| 21:37 | 23.37       | Viimeinen päivitys ennen otteluita: maalivahdit, kertoimet |

Viikonloppuisin on lisäksi ajo 12:17 UTC (14.17), jos ensimmäinen peli alkaa sen jälkeen. Manuaaliset ajot löytyvät välilehdeltä **Actions**: `backfill` (historian lataus) ja `train_xg` (xG-mallin uudelleenopetus).

## Kertoimet (The Odds API)

Kertoimet haetaan The Odds API -palvelusta. Avain annetaan GitHubiin kohdassa Settings → Secrets and variables → Actions → `ODDS_API_KEY`. Paikallisesti avain annetaan vain ympäristömuuttujana, ei koskaan tiedostoon.

Kertoimia haetaan kello 17.17 ja 23.37 Suomen aikaa sekä manuaaliajoissa. Muut ajot käyttävät viimeisintä hakua, jos se on alle 36 tuntia vanha. Haun epäonnistuminen ei kaada ajoa. Kuukaudessa kuluu noin 186 krediittiä 500:sta.

Lähde ei tarjoa NHL:n 1X2-kertoimia (kotivoitto, tasapeli, vierasvoitto) perusmarkkinana. Sivu näyttää siksi mallin laskemat 1X2-kertoimet ilman marginaalia sekä pienimmän Veikkauksen kertoimen, jolla vedosta voi pitää arvopelinä.

## Pelaajaennusteet

Pelaajan piste- ja maalitodennäköisyys (vähintään yksi piste tai maali) lasketaan joukkueen odotetuista maaleista. Kenttäpelaajien maalit ja pisteet per 60 minuuttia tulevat painotetuista kausitiedoista, ja ne kutistetaan kohti saman position keskiarvoa. Maalit jaetaan kokoonpanon pelaajille tasojen ja jääajan mukaan, ja todennäköisyydet saadaan binomijakaumasta.

Parametrit viritetään kaudella 2024-25 ja julkaisuehto arvioidaan kaudella 2025-26:

```bash
python -m pipeline.player_backtest --tune-season 20242025 --gate-season 20252026
```

Sivu näyttää pelaajat vasta, kun `model/player_params.json` sisältää `gate.passed`-arvon tosi. Kaudella 2025-26 ehto täyttyi: P(vähintään 1 piste) Brier 0,206 (lähtötaso 0,214) ja P(vähintään 1 maali) Brier 0,123 (lähtötaso 0,128). Parhaiden pelaajien todennäköisyydet ovat hieman varovaisia: toteutunut osuus on 45–60 %:n luokissa 4–6 prosenttiyksikköä korkeampi.

Kokoonpano on arvio (tila "Arvio"). Se johdetaan joukkueen viimeisimmästä pelistä, eikä loukkaantumislähdettä ole. Pelaaja, joka ei pukeutunut viimeisimpään peliin, ei ole mukana.

## Ottelunäkymä

Ottelusivulla on osiot: **Nostot** (sääntöpohjaiset huomiot), **Maalivahdit ja poissaolot** (maalivahdit, poissaolot ja kokoonpanomuutokset), **Vire**, **Kausivertailu** ja **Keskinäiset** (enintään 8 viimeisintä kohtaamista kahdelta välimuistissa olevalta kaudelta). Nostojen tekstit kirjoitetaan ilman taivutettuja numeroita, ja koti/vieras-ero näytetään vain, kun kummaltakin puolelta on vähintään 5 ottelua.

Maalivahtien tila on aina "Arvio". Poissaolot näytetään vaikutuksineen, ja ESPN:n mukaan poissa olevat poistetaan pelaajaennusteista. Kokoonpano- ja H2H-kertoimet eivät muuta joukkueen λ:aa, koska ne eivät läpäisseet julkaisuehtoa.

## Malli-välilehti

Malli-välilehti näyttää tiedoston `site/data/performance.json` suorituskyvyn. Sivulla on kauden ratkenneiden otteluiden kotivoitto-% perustasona ja tasapanosten ROI, jossa push kuuluu otokseen. Betfairin voitoista vähennetään 5 %:n provisio.

## Kokoonpanoportin uudelleenajo

Kokoonpano- ja H2H-kertoimien julkaisuehdon tulos on tiedostossa `model/lineup_params.json`. Ehto ajetaan uudelleen komennolla:

```bash
python -m pipeline.lineup_backtest --tune-season 20242025 --gate-season 20252026
```

## Jos ajo epäonnistuu tai tarkistus hälyttää

GitHub lähettää sähköpostin, kun ajo päättyy virheeseen. Lue `reports/review.md`, korjaa ongelma paikallisesti, aja `python tools/review.py` uudelleen ja työnnä korjaus repoon. Jos tarkistus löytää virheen (koodi 2), julkaisua ei tehdä ja edellinen sivu jää näkyviin.

## 60 päivän sääntö

GitHub poistaa ajastetut työnkulut käytöstä vasta 60 päivän toimettomuuden jälkeen, eikä se koske tätä repoa, koska jokainen putkiajo kommitoi `data/state/sources.json`-tiedoston ja tuo siten aktiivisuutta, joten erillistä uudelleenaktivointia ei tarvita.
