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

## Fase 2 (pendent)

- **Camp de vents interpolat** (`vent_privat.enc`): cal generar `vent_grid.npz`
  amb el relleu de la CV (la targeta està amagada al visor).
- **Tauler d'incendis + WindNinja**: cal desplegar un Worker de Cloudflare
  propi per a este repo i tornar a posar `WORKER_URL` a `privat.html`.
- **QPE de radar**: cal replicar la branca `qpe` (l'opció està llevada del
  desplegable).
