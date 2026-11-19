# Kokoonpano- ja keskinäiset-kertoimet: portti 20252026

Viritys 20242025 toteutuneilla kokoonpanoilla (maalivahdit ennustettu, 1000 simulaatiota): rho 0.30.

| rho | Voittaja log loss | 1X2 log loss |
|---|---|---|
| baseline | 0.66618 | 1.03758 |
| 0.30 | 0.66121 | 1.03284 |
| 0.45 | 0.66201 | 1.03438 |
| 0.60 | 0.66250 | 1.03261 |

Portti 20252026 ennustetuilla kokoonpanoilla (2000 simulaatiota, samat siemenet kaikille):

| Malli | n | Voittaja log loss | 1X2 log loss | Brier |
|---|---|---|---|---|
| Perusmalli | 1312 | 0.67905 | 1.06281 | 0.24311 |
| Kokoonpano (ennustettu) | 1312 | 0.68008 | 1.06188 | 0.24357 |
| Keskinäiset | 1312 | 0.67917 | 1.06203 | 0.24318 |
| Kokoonpano + keskinäiset | 1312 | 0.68005 | 1.06199 | 0.24355 |

Kokoonpanokerroin: EI KÄYTÖSSÄ (vaatii paremman voittaja- ja 1X2-log lossin kuin perusmalli).
Keskinäiset-kerroin: EI KÄYTÖSSÄ (vaatii paremman voittaja-log lossin valitun mallin päällä).
