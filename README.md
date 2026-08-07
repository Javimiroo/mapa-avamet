# Mapa CV · AVAMET

Mapa privat d'observació meteorològica de la **Comunitat Valenciana** amb les
~900 estacions de la **MeteoXarxa d'AVAMET**. Rèplica del mapa de Catalunya
(`mapa-aemet/privat.html`): llindars, filtres combinats, ratxes, comptador
d'hores, barbes de vent, màquina del temps, meteogrames comparatius, arxiu
històric per dies i radar/echotop d'AEMET.

**Dades: AVAMET (CC BY-NC-ND) — ús intern no comercial; per això el mapa va
xifrat amb contrasenya.**

## Com funciona

- `fetch_avamet.py` baixa **una sola pàgina** (`mxo-mxo.php`, taula amb totes
  les estacions) cada 30 min, la parseja, acumula l'històric (3 dies vius),
  deriva la precipitació per intervals del total diari, calcula acumulats
  (1h/3h/6h/24h/dia/7d) i xifra el resultat → `dades_privat.enc`.
- Coordenades a `avamet_estacions.json` (caché; les estacions noves es busquen
  soles a la fitxa tècnica i s'hi afigen).
- Workflow `avamet.yml`: dades vives a la branca **`dades`** (force-push, el
  repo no creix) i arxiu diari congelat a la branca **`arxiu`**.
- `privat.html` (GitHub Pages) desxifra al navegador amb la contrasenya.

## Posada en marxa (una sola vegada)

1. Crear el repo **públic** `Javimiroo/mapa-avamet` i pujar-hi estos fitxers
   (des de PowerShell, NO des del sandbox):
   ```powershell
   cd mapa-avamet
   git init -b main
   git add -A
   git commit -m "Mapa CV AVAMET"
   git remote add origin https://github.com/Javimiroo/mapa-avamet.git
   git push -u origin main
   ```
2. **Secret**: Settings → Secrets and variables → Actions → New repository
   secret → `MAPA_PASS` (pots posar la mateixa contrasenya que al mapa de
   Catalunya i així l'equip només en recorda una).
3. **Pages**: Settings → Pages → Deploy from a branch → `main` / root.
4. Executar el workflow una vegada a mà: Actions → *Actualitza mapa CV
   (AVAMET)* → **Run workflow** (sempre "Run workflow" nou, mai "Re-run").
5. Obrir `https://javimiroo.github.io/mapa-avamet/privat.html`.
6. (Opcional, recomanat) Disparador extern a **cron-job.org** cada 30 min,
   com als altres repos: POST a
   `https://api.github.com/repos/Javimiroo/mapa-avamet/actions/workflows/avamet.yml/dispatches`
   amb body `{"ref":"main"}` i el PAT fine-grained (Actions RW d'este repo).

## Comprovació important (primer run)

El sandbox de desenvolupament no arriba a avamet.org, així que el parser està
provat amb HTML real capturat però **el primer run a GitHub Actions és la
prova de foc**: si AVAMET bloquejara les IPs de datacenter, el log diria
"AVAMET ha fallat". En eixe cas ho resoldrem (canvi d'User-Agent, mirall, o
baixada des d'un altre punt).

## Camp de vents (activar una vegada)

1. Mou `gen_grid.yml` a `.github\workflows\gen_grid.yml` i puja-ho.
2. Actions → **Genera la graella de terreny** → Run workflow (una sola vegada).
   Baixa el relleu de la CV i commiteja `vent_grid.npz` a main (~1 min).
3. A partir del run següent del workflow principal, `vent_privat.enc` es genera
   sol (càlcul **incremental**: només l'hora nova, els fotogrames vells es
   reutilitzen) i la targeta «🌬️ Camp de vents» del visor cobra vida. També es
   va omplint `arxiu-vent/` (1 dia tancat per run).

## Worker (botó Actualitzar + incendis compartits)

Cal per al botó «⬇ Actualitzar» i per a la targeta «🔥 Incendi» (els incendis
es guarden en un KV de Cloudflare i els veu tot l'equip). Instruccions pas a
pas a la capçalera de `worker_avamet.js` (nom del worker: **actualitza-avamet**,
que és la URL que ja porta `privat.html`).

## Pluja

- Variable **Precipitació (última 1/2 h)**: el que ha caigut entre execucions
  (derivat del total diari d'AVAMET, tolerant al reset de mitjanit).
- Variable **Precipitació acumulada** (1h/3h/6h/24h/dia/7d): per estació, SENSE
  superfície interpolada. Els períodes llargs es van completant a mesura que
  l'històric acumula dies.

## Previsió WindNinja per zona (tauler d'incendi)

Portada a AVAMET: `windninja_prep.py` s'inicialitza amb les estacions AVAMET de
la publicació dins de la caixa (o, si no n'hi ha cap, amb el vent de la més
propera). Workflow `windninja_zona_wf.yml` → moure'l a
`.github\workflows\windninja_zona.yml`. El Worker ja el dispara pel nom per
defecte, no cal tocar-li res. Els botons de previsió del tauler d'incendi
queden operatius.

## QPE de radar

Descartada de moment per decisió de Javi.
