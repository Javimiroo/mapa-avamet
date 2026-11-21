# -*- coding: utf-8 -*-
"""
Prepara el mapa de models de combustible de la Comunitat Valenciana per al simulador.

Font: «Modelos de combustible (Scott & Burgan) del territorio de la Comunidad Valenciana», ràster de 10 m de la
Direcció General de Prevenció d'Incendis Forestals, publicat per l'ICV (llicència CC BY):
    https://dadesobertes.gva.es/es/dataset/modelos-de-combustible-scott-burgan-del-territorio-de-la-comunidad-valenciana1

Què fa: baixa el ZIP, recodifica els valors a classes compactes i rebaixa de 10 m a 20 m (majoria de cada 2x2),
i escriu un GeoTIFF uint8 comprimit (~pocs MB) que el workflow de WindNinja retalla per a cada incendi.

Classes d'eixida (uint8):
    0        fora de l'àmbit / sense dada
    1..14    GR2 GR4 GR7 GR8 SB3 SH1 SH3 SH4 SH5 SH9 TU1 TU2 TU3 TU5   (mateix ordre que els codis 1..14 de l'ICV)
    15       no combustible (NB1, NB3, NB8, NB9: urbà, aigua, roca, arrossar, vies…)
    16       agrícola segons SIGPAC (codis 100..118 de l'ICV, que el mapa dona com a no combustible NB3)
Els codis 200..219 (terres arables SIGPAC) són GR2 al mapa original -> classe 1.

Ús:
    python combustible_prep.py --out combustible_20m.tif            # baixa i processa
    python combustible_prep.py --zip mc.zip --out combustible_20m.tif
"""
import os, io, sys, zipfile, argparse, tempfile, urllib.request
import numpy as np
import rasterio
from rasterio.windows import Window
from rasterio.transform import Affine

URL = ("https://descargas.icv.gva.es/dcd/05_medioambiente/09_incendios/ModCombustibles/"
       "0509_2021MC_CV_ras_TIF.zip?tipo=directa&formato=tif")
NOMS = ["", "GR2", "GR4", "GR7", "GR8", "SB3", "SH1", "SH3", "SH4", "SH5", "SH9", "TU1", "TU2", "TU3", "TU5", "NB", "AGR"]


def lut():
    t = np.zeros(1024, np.uint8)
    for v in range(1, 15):
        t[v] = v
    for v in range(15, 22):
        t[v] = 15
    for v in range(100, 119):
        t[v] = 16
    for v in range(200, 220):
        t[v] = 1
    return t


def majoria2x2(a):
    """Rebaixa a la meitat agafant el valor majoritari de cada bloc 2x2 (en empat, el de dalt-esquerra)."""
    h, w = a.shape
    a = a[:h - h % 2, :w - w % 2]
    b = a.reshape(h // 2, 2, w // 2, 2).transpose(0, 2, 1, 3).reshape(h // 2, w // 2, 4)
    out = b[:, :, 0].copy()
    # un valor guanya si apareix >= 2 vegades i el primer només 1
    for k in (1, 2, 3):
        cnt_k = (b == b[:, :, k:k + 1]).sum(axis=2)
        cnt_0 = (b == out[:, :, None]).sum(axis=2)
        out = np.where(cnt_k > cnt_0, b[:, :, k], out)
    return out


def processa(tif, out):
    T = lut()
    with rasterio.open(tif) as ds:
        W2, H2 = ds.width // 2, ds.height // 2
        tr = ds.transform * Affine.scale(2, 2)
        prof = dict(driver="GTiff", dtype="uint8", count=1, width=W2, height=H2, crs=ds.crs, transform=tr,
                    nodata=0, compress="deflate", predictor=1, tiled=True, blockxsize=256, blockysize=256, zlevel=9)
        hist = np.zeros(17, np.int64)
        with rasterio.open(out, "w", **prof) as dst:
            pas = 4096                                   # files d'entrada per tira (parell)
            for r0 in range(0, H2 * 2, pas):
                n = min(pas, H2 * 2 - r0)
                a = ds.read(1, window=Window(0, r0, W2 * 2, n))
                a = np.where((a < 0) | (a > 1023), 0, a)
                b = majoria2x2(T[a])
                dst.write(b, 1, window=Window(0, r0 // 2, W2, n // 2))
                hist += np.bincount(b.ravel(), minlength=17)[:17]
            dst.update_tags(font="ICV / GVA - Modelos de combustible Scott & Burgan 2021 (CC BY)",
                            classes=",".join(NOMS))
    tot = hist[1:].sum()
    print("escrit %s (%.1f MB) · %dx%d a 20 m" % (out, os.path.getsize(out) / 1e6, W2, H2))
    for k in range(1, 17):
        print("  %2d %-4s %5.1f %%" % (k, NOMS[k], 100.0 * hist[k] / max(1, tot)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default=None, help="ZIP ja baixat (si no, es baixa de l'ICV)")
    ap.add_argument("--out", default="combustible_20m.tif")
    a = ap.parse_args()
    tmp = tempfile.mkdtemp()
    z = a.zip
    if not z:
        z = os.path.join(tmp, "mc.zip")
        print("baixant", URL)
        req = urllib.request.Request(URL, headers={"User-Agent": "pyromet"})
        with urllib.request.urlopen(req, timeout=600) as r, open(z, "wb") as f:
            while True:
                blk = r.read(1 << 20)
                if not blk:
                    break
                f.write(blk)
        print("  %.1f MB" % (os.path.getsize(z) / 1e6))
    with zipfile.ZipFile(z) as zf:
        nom = [n for n in zf.namelist() if n.lower().endswith(".tif")]
        if not nom:
            raise SystemExit("el ZIP no porta cap .tif")
        zf.extract(nom[0], tmp)
    processa(os.path.join(tmp, nom[0]), a.out)


if __name__ == "__main__":
    main()
