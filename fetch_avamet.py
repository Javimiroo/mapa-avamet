# -*- coding: utf-8 -*-
"""
Baixa la taula MXO d'AVAMET (totes les estacions de la Comunitat Valenciana en una
sola pàgina), construeix el mateix esquema d'estacions que el mapa de Catalunya
(privat.html) i XIFRA el resultat amb la contrasenya -> dades_privat.enc

Font: https://www.avamet.org/mxo-mxo.php  (HTML públic, ~2 MB, s'actualitza cada 5 min)
Metadades (coordenades): avamet_estacions.json (cachejades; les noves estacions es
busquen a mx-fitxa.php?id=... i s'afigen a la caché).

Dades AVAMET: llicència CC BY-NC-ND — ús intern no comercial, mapa xifrat, citada la font.

Variables d'entorn:
    MAPA_PASS   contrasenya del mapa (secret a GitHub o 'set' en local)

Columnes de la taula MXO (per fila d'estació, 15 cel·les):
    0 nom (municipi + <span> detall)   1 alt (m, + alçada garita)
    2 ta   3 tamin(dia)   4 tamax(dia)   5 tpr (punt de rosada)
    6 heat index   7 hr   8 prec dia (mm)   9 int. precipitació
    10 vv (km/h)   11 dv (text 16 sectors)   12 vmax (km/h)   13 webcam   14 rellotge

L'històric es construeix ACUMULANT una lectura per execució (cada ~30 min), com es
fa amb el 'previ' al mapa de Catalunya. La precipitació de cada interval es deriva
del total diari (resta amb l'execució anterior, tolerant al reset de mitjanit).
"""

import base64
import json
import os
import re
import ssl
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _need(nom):
    v = os.environ.get(nom)
    if not v:
        raise SystemExit("Falta la variable d'entorn %s (secret a GitHub o 'set' en local)." % nom)
    return v


PASSWORD = _need("MAPA_PASS")

MXO_URL = "https://www.avamet.org/mxo-mxo.php"
FITXA_URL = "https://www.avamet.org/mx-fitxa.php?id="
EST_FILE = "avamet_estacions.json"
OUT_FILE = "dades_privat.enc"
ITER = 200000
TZ_LOCAL = ZoneInfo("Europe/Madrid")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) graf-avamet/1.0"

# La publicació actual (branca 'dades'): per reutilitzar-la si la font falla i per
# NO publicar mai una foto més vella que la que ja hi ha (blindatge anti-regressió).
DADES_PREV_URL = "https://raw.githubusercontent.com/Javimiroo/mapa-avamet/dades/dades_privat.enc"

# 16 sectors -> graus (convenció meteorològica: d'on ve el vent)
DIRS = {"N": 0, "NNE": 22.5, "NE": 45, "ENE": 67.5, "E": 90, "ESE": 112.5,
        "SE": 135, "SSE": 157.5, "S": 180, "SSO": 202.5, "SO": 225, "OSO": 247.5,
        "O": 270, "ONO": 292.5, "NO": 315, "NNO": 337.5}

_SSL = ssl.create_default_context()


# ============================ utilitats ============================
def _num(s):
    """'26,3' -> 26.3 ; '' / '-' -> None"""
    if s is None:
        return None
    s = str(s).strip().replace(".", "").replace(",", ".") if re.match(r"^-?\d{1,3}(\.\d{3})+,\d", str(s).strip()) \
        else str(s).strip().replace(",", ".")
    if not s or s in ("-", "--"):
        return None
    try:
        return round(float(s), 1)
    except ValueError:
        return None


def _get_text(url, tries=4):
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ca,es"})
            raw = urllib.request.urlopen(req, timeout=60, context=_SSL).read()
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("latin-1")
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429 or 500 <= e.code < 600:
                time.sleep(10); continue
            raise
        except urllib.error.URLError as e:
            last = e; time.sleep(8); continue
    raise RuntimeError("massa reintents (%s): %s" % (last, url))


def _parse_t(t):
    if not t:
        return None
    s = t.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if len(s) >= 5 and s[-5] in "+-" and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]
    try:
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


# ============================ metadades (coordenades) ============================
def carrega_meta():
    if os.path.exists(EST_FILE):
        with open(EST_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def fitxa_coords(idest):
    """lat/lon (i altitud si es troba) de la fitxa tècnica d'una estació nova."""
    try:
        txt = _get_text(FITXA_URL + idest, tries=2)
        la = re.search(r"lat\s*=\s*(-?\d+\.\d+)", txt)
        lo = re.search(r"lon\s*=\s*(-?\d+\.\d+)", txt)
        if la and lo:
            return float(la.group(1)), float(lo.group(1))
    except Exception as ex:  # noqa
        print("  avis: fitxa de %s no llegida (%s)" % (idest, str(ex)[:60]))
    return None


# ============================ parser de la taula MXO ============================
RE_COMARCA = re.compile(r'<td class="rComarca"[^>]*>([^<]+)</td>')
RE_ID = re.compile(r'id=(c\d{2}m\d{3}e\d{2})')
RE_TD = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
RE_TAG = re.compile(r'<[^>]+>')
RE_NOM = re.compile(r'>([^<>]*)<span class="rEstaDmxo"><span class="ptda"></span>([^<>]*)</span>', re.S)
RE_UPD = re.compile(r'actuali[tz]+[sz]?ad[ae]s?:\s*(\d{2})-(\d{2})-(\d{4})\s+(\d{1,2}):(\d{2})', re.I)


def _cel_text(html_cel):
    import html as _html
    return _html.unescape(RE_TAG.sub("", html_cel).replace("&nbsp;", " ")).strip()


def parseja_mxo(html):
    """Retorna (files, fint_iso). files: llista de dicts crus per estació."""
    # hora de generació de la pàgina (hora local Europe/Madrid) -> ISO UTC
    fint = None
    m = RE_UPD.search(html)
    if m:
        dd, mm, yy, hh, mi = (int(x) for x in m.groups())
        try:
            fint = datetime(yy, mm, dd, hh, mi, tzinfo=TZ_LOCAL).astimezone(timezone.utc)
        except ValueError:
            fint = None
    if fint is None:
        fint = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    fint_iso = fint.isoformat().replace("+00:00", "Z")

    html = re.sub(r'<!--.*?-->', '', html, flags=re.S)      # fora comentaris (hi ha <td> comentats!)
    files = []
    comarca = ""
    # recorrem les files en ordre per mantenir la comarca activa
    for tr in re.findall(r'<tr[^>]*>.*?</tr>', html, flags=re.S):
        mc = RE_COMARCA.search(tr)
        if mc:
            comarca = _cel_text(mc.group(1))
            continue
        if 'rEsta' not in tr:
            continue
        mid = RE_ID.search(tr)
        if not mid:
            continue
        cels = RE_TD.findall(tr)
        if len(cels) < 13:               # taules laterals de rànquings: fora
            continue
        mn = RE_NOM.search(tr)
        if mn:
            import html as _html
            muni = _html.unescape(mn.group(1)).strip()
            detall = _html.unescape(mn.group(2)).strip()
            nom = (muni + (" · " + detall if detall else "")).strip()
        else:
            nom = _cel_text(cels[0])
        alt = None
        malt = re.match(r"\s*(\d+)", _cel_text(cels[1]))
        if malt:
            alt = int(malt.group(1))
        dvtxt = _cel_text(cels[11]).upper()
        files.append({
            "id": mid.group(1), "nom": nom, "comarca": comarca, "alt": alt,
            "ta": _num(_cel_text(cels[2])), "tamin": _num(_cel_text(cels[3])),
            "tamax": _num(_cel_text(cels[4])), "tpr": _num(_cel_text(cels[5])),
            "hr": _num(_cel_text(cels[7])), "prec_dia": _num(_cel_text(cels[8])),
            "vv": _num(_cel_text(cels[10])), "dv": DIRS.get(dvtxt),
            "vmax": _num(_cel_text(cels[12])),
        })
    return files, fint_iso


# ============================ xifratge ============================
def xifrar(text, password):
    salt = os.urandom(16); iv = os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER).derive(password.encode())
    ct = AESGCM(key).encrypt(iv, text.encode("utf-8"), None)
    return {"v": 1, "kdf": "PBKDF2-SHA256", "it": ITER, "alg": "AES-GCM",
            "salt": base64.b64encode(salt).decode(), "iv": base64.b64encode(iv).decode(),
            "ct": base64.b64encode(ct).decode()}


def desxifrar(blob, password):
    salt = base64.b64decode(blob["salt"]); iv = base64.b64decode(blob["iv"]); ct = base64.b64decode(blob["ct"])
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=blob.get("it", ITER)).derive(password.encode())
    return json.loads(AESGCM(key).decrypt(iv, ct, None).decode("utf-8"))


def carrega_estacions_previ():
    """{idema: estació completa} de la PUBLICACIÓ ACTUAL (o del fitxer local si hi és)."""
    for origen in ("url", "file"):
        try:
            if origen == "url":
                req = urllib.request.Request(DADES_PREV_URL + "?_=" + str(int(time.time())),
                                             headers={"User-Agent": UA})
                blob = json.loads(urllib.request.urlopen(req, timeout=30, context=_SSL).read())
            else:
                if not os.path.exists(OUT_FILE):
                    continue
                with open(OUT_FILE, encoding="utf-8") as f:
                    blob = json.load(f)
            prev = desxifrar(blob, PASSWORD)
            return {e["idema"]: e for e in prev.get("estacions", [])}
        except Exception as ex:  # noqa
            print("  avis: previ (%s) no llegit (%s)" % (origen, str(ex)[:80]))
    return {}


# ============================ construcció de les estacions ============================
def estacions_avamet(meta, prev_full):
    html = _get_text(MXO_URL)
    files, fint_iso = parseja_mxo(html)
    if len(files) < 100:
        raise RuntimeError("només %d files parsejades: pàgina inesperada" % len(files))
    print("  MXO: %d estacions a la taula · pàgina de les %s" % (len(files), fint_iso))

    # metadades noves (estacions que no són a la caché)
    noves = [f["id"] for f in files if f["id"] not in meta]
    for idest in noves[:25]:                       # com a molt 25 fitxes noves per execució
        c = fitxa_coords(idest)
        if c:
            f = next(x for x in files if x["id"] == idest)
            meta[idest] = {"nom": f["nom"], "comarca": f["comarca"], "alt": f["alt"],
                           "lat": c[0], "lon": c[1]}
            print("  nova estació: %s (%s)" % (idest, f["nom"]))
    if noves:
        with open(EST_FILE, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)

    out = []
    for f in files:
        m = meta.get(f["id"])
        if not m or m.get("lat") is None:
            continue
        # precipitació de l'interval: resta del total diari amb l'execució anterior
        prev = prev_full.get("AV_" + f["id"]) or {}
        p_ant = (prev.get("actual") or {}).get("prec_dia")
        prec_int = None
        if f["prec_dia"] is not None:
            if p_ant is None:
                prec_int = None                    # primera vegada: desconegut
            elif f["prec_dia"] >= p_ant:
                prec_int = round(f["prec_dia"] - p_ant, 1)
            else:
                prec_int = f["prec_dia"]           # reset de mitjanit
        row = {
            "t": fint_iso, "ta": f["ta"], "tamax": None, "tamin": None,
            "hr": f["hr"], "vv": f["vv"], "vmax": f["vmax"], "dv": f["dv"],
            "prec": prec_int, "pres": None, "tpr": f["tpr"],
        }
        out.append({
            "idema": "AV_" + f["id"], "nom": f["nom"] or m.get("nom") or f["id"],
            "provincia": f["comarca"] or m.get("comarca") or "",
            "font": "AVAMET", "lat": m["lat"], "lon": m["lon"],
            "alt": f["alt"] if f["alt"] is not None else m.get("alt"),
            "actual": {
                "fint": fint_iso, "ta": f["ta"], "tamax": None, "tamin": None,
                "tamax_dia": f["tamax"], "tamin_dia": f["tamin"], "n_hores": 0,
                "hr": f["hr"], "vv": f["vv"], "vmax": f["vmax"], "dv": f["dv"],
                "dmax": None, "prec": prec_int, "prec_dia": f["prec_dia"],
                "pres": None, "tpr": f["tpr"],
            },
            "historic": [row],
        })
    return out


# ============================ acumulació de l'històric ============================
DIES_HISTORIC = 3   # històric VIU (meteogrames avui+ahir, ratxes 24 h, comptador)


def acumula(estacions, previ):
    ara = datetime.now(timezone.utc)
    tall = ara - timedelta(days=DIES_HISTORIC)
    t24 = ara - timedelta(hours=24)
    for e in estacions:
        byt = {}
        for row in previ.get(e["idema"], []) + e.get("historic", []):
            t = row.get("t")
            if t:
                byt[t] = row
        rows = []
        for t in sorted(byt):
            d = _parse_t(t)
            if d and d >= tall:
                rows.append(byt[t])
        e["historic"] = rows
        # màx/mín 24 h de reserva (si la pàgina no ha donat tamax/tamin del dia)
        if e["actual"].get("tamax_dia") is None or e["actual"].get("tamin_dia") is None:
            vals = [r["ta"] for r in rows if r.get("ta") is not None
                    and (_parse_t(r.get("t")) or ara) >= t24]
            if vals:
                e["actual"].setdefault("tamax_dia", None)
                if e["actual"]["tamax_dia"] is None:
                    e["actual"]["tamax_dia"] = round(max(vals), 1)
                if e["actual"]["tamin_dia"] is None:
                    e["actual"]["tamin_dia"] = round(min(vals), 1)
        e["actual"]["n_hores"] = len(rows)


def acumulats_precipitacio(estacions):
    """pacum 1h/3h/6h/24h/dia/7d per estació, a partir dels intervals acumulats a
    l'històric ('dia' ve directament de la pàgina). 24h/7d milloren a mesura que
    l'arxiu viu acumula dies (màxim DIES_HISTORIC dies de finestra)."""
    n = 0
    for e in estacions:
        rows = e.get("historic", [])
        parells = [(_parse_t(r.get("t")), r.get("prec")) for r in rows]
        parells = [(t, p) for (t, p) in parells if t is not None and p is not None and p >= 0]
        act = e.get("actual") or {}
        tref = _parse_t(act.get("fint")) or datetime.now(timezone.utc)

        def suma(hores):
            des = tref - timedelta(hours=hores)
            return round(sum(p for (t, p) in parells if t > des), 1)

        pac = {"1h": suma(1), "3h": suma(3), "6h": suma(6), "24h": suma(24), "7d": suma(24 * 7)}
        pac["dia"] = act.get("prec_dia") if act.get("prec_dia") is not None else suma(24)
        act["pacum"] = pac
        n += 1
    print("  precipitació acumulada: %d estacions" % n)


# ============================ arxiu històric (per dia, congelat) ============================
ARXIU_DIR = "arxiu"


def _dia_local(t):
    d = _parse_t(t)
    return d.astimezone(TZ_LOCAL).date().isoformat() if d else None


def arxiva(estacions):
    os.makedirs(ARXIU_DIR, exist_ok=True)
    avui = datetime.now(timezone.utc).astimezone(TZ_LOCAL).date()
    limit = avui                      # arxiva ahir i anteriors (congelats)

    dies = set()
    for e in estacions:
        for r in e.get("historic", []):
            dk = _dia_local(r.get("t"))
            if dk:
                dies.add(dk)

    nous = 0
    for dk in sorted(dies):
        try:
            d_date = datetime.fromisoformat(dk).date()
        except ValueError:
            continue
        if d_date >= limit:
            continue
        path = os.path.join(ARXIU_DIR, dk + ".enc")
        if os.path.exists(path):
            continue
        ests = []
        for e in estacions:
            rows = [r for r in e.get("historic", []) if _dia_local(r.get("t")) == dk]
            if not rows:
                continue
            ests.append({
                "idema": e["idema"], "nom": e.get("nom"), "provincia": e.get("provincia"),
                "font": e.get("font"), "lat": e.get("lat"), "lon": e.get("lon"), "alt": e.get("alt"),
                "historic": rows,
            })
        if not ests:
            continue
        dia_obj = {
            "dia": dk,
            "generat": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "n_estacions": len(ests), "estacions": ests,
        }
        blob = xifrar(json.dumps(dia_obj, ensure_ascii=False, separators=(",", ":")), PASSWORD)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(blob, f)
        nous += 1
        print("  arxivat %s (%d estacions)" % (dk, len(ests)))

    disponibles = sorted(fn[:-4] for fn in os.listdir(ARXIU_DIR) if fn.endswith(".enc"))
    with open(os.path.join(ARXIU_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"dies": disponibles,
                   "actualitzat": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")},
                  f, ensure_ascii=False)
    print("  arxiu: %d dies nous · %d dies en total" % (nous, len(disponibles)))


# ============================ principal ============================
def main():
    meta = carrega_meta()
    print("Metadades: %d estacions a la caché" % len(meta))

    prev_full = carrega_estacions_previ()
    previ = {k: v.get("historic", []) for k, v in prev_full.items()}

    print("Baixant AVAMET (MXO)...")
    _t = time.perf_counter()
    try:
        estacions = estacions_avamet(meta, prev_full)
    except Exception as ex:
        estacions = None
        print("  AVÍS: AVAMET ha fallat (%s)." % str(ex)[:120])
    print("  ⏱ AVAMET: %.1f s" % (time.perf_counter() - _t))

    if not estacions:
        estacions = list(prev_full.values())
        if not estacions:
            raise SystemExit("AVAMET no ha respost i no hi ha dades prèvies; no s'escriu res.")
        print("  reutilitzant la publicació anterior (%d estacions, no actualitzat)" % len(estacions))
    else:
        # BLINDATGE ANTI-REGRESSIÓ: mai publicar una foto més vella que la publicada
        n_blind = 0
        for e in estacions:
            p = prev_full.get(e["idema"])
            if not p:
                continue
            fn = _parse_t((e.get("actual") or {}).get("fint"))
            fp = _parse_t((p.get("actual") or {}).get("fint"))
            if fp and (not fn or fp > fn):
                e["actual"] = dict(p["actual"])
                e["historic"] = []
                n_blind += 1
        if n_blind:
            print("  ⚠ blindatge: %d estacions conservades de la publicació" % n_blind)
        # estacions que han desaparegut de la taula: es conserven (quedaran 'desfasades')
        vives = {e["idema"] for e in estacions}
        recuperades = 0
        for k, v in prev_full.items():
            if k not in vives:
                estacions.append(v)
                recuperades += 1
        if recuperades:
            print("  conservades %d estacions absents de la taula (desfasades)" % recuperades)

    estacions.sort(key=lambda e: e["nom"])
    print("Acumulant històric (fins a %d dies)..." % DIES_HISTORIC)
    acumula(estacions, previ)
    acumulats_precipitacio(estacions)
    nh = [e["actual"]["n_hores"] for e in estacions] or [0]
    print("  lectures/estació -> min %d · màx %d · mitjana %d" % (min(nh), max(nh), sum(nh) // len(nh)))

    dades = {
        "generat": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "font": "AVAMET (MeteoXarxa) - dades CC BY-NC-ND, us intern no comercial",
        "n_estacions": len(estacions),
        "n_avamet": len(estacions),
        "estacions": estacions,
    }
    blob = xifrar(json.dumps(dades, ensure_ascii=False, separators=(",", ":")), PASSWORD)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(blob, f)
    print("OK -> %s  (%d estacions AVAMET)" % (OUT_FILE, len(estacions)))

    print("Arxivant històric per dies...")
    try:
        arxiva(estacions)
    except Exception as ex:
        print("  avis: arxiu no completat (%s)" % ex)


if __name__ == "__main__":
    main()
