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

## Jos ajo epäonnistuu tai tarkistus hälyttää

GitHub lähettää sähköpostin, kun ajo päättyy virheeseen. Lue `reports/review.md`, korjaa ongelma paikallisesti, aja `python tools/review.py` uudelleen ja työnnä korjaus repoon. Jos tarkistus löytää virheen (koodi 2), julkaisua ei tehdä ja edellinen sivu jää näkyviin.

## 60 päivän sääntö

GitHub poistaa ajastetut työnkulut käytöstä vasta 60 päivän toimettomuuden jälkeen, eikä se koske tätä repoa, koska jokainen putkiajo kommitoi `data/state/sources.json`-tiedoston ja tuo siten aktiivisuutta, joten erillistä uudelleenaktivointia ei tarvita.
