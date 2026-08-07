# -*- coding: utf-8 -*-
"""
Genera vent_grid.npz per a la Comunitat Valenciana: graella de terreny que fa
servir camp_vents.py (H en metres, màscara de mar, bbox, dx/dy en metres).

Relleu: tiles "terrarium" (AWS elevation-tiles-prod, les mateixes del visor 3D).
S'executa UNA VEGADA amb el workflow gen_grid.yml (workflow_dispatch) i el
resultat es commiteja a main. No cal tornar-lo a executar mai més.

Format (idèntic al de Catalunya):
    H    float32 (ny, nx)  altitud en m (mar = 0), fila 0 = SUD
    sea  uint8   (ny, nx)  1 = mar
    bbox float64 [LON0, LON1, LAT0, LAT1]
    nx, ny, dx, dy
"""
import io
import math
import urllib.request

import numpy as np
from PIL import Image

# bbox de la Comunitat Valenciana (amb un poc de marge)
LON0, LON1 = -1.60, 0.75
LAT0, LAT1 = 37.75, 40.90
DX_M = 750.0            # resolució del solver (~750 m; equilibri precisió/temps)
ZOOM = 9                # terrarium z9 ≈ 240 m/px a estes latituds (de sobra)

TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"


def tile_xy(lat, lon, z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    r = math.radians(lat)
    y = (1.0 - math.log(math.tan(r) + 1.0 / math.cos(r)) / math.pi) / 2.0 * n
    return x, y


def baixa_mosaic(z):
    x0f, y1f = tile_xy(LAT0, LON0, z)   # SO
    x1f, y0f = tile_xy(LAT1, LON1, z)   # NE
    tx0, tx1 = int(math.floor(x0f)), int(math.floor(x1f))
    ty0, ty1 = int(math.floor(y0f)), int(math.floor(y1f))
    W = (tx1 - tx0 + 1) * 256
    Hpx = (ty1 - ty0 + 1) * 256
    mos = np.zeros((Hpx, W), dtype=np.float32)
    print("mosaic: tiles x %d..%d, y %d..%d (%dx%d px)" % (tx0, tx1, ty0, ty1, W, Hpx))
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            url = TILE_URL.format(z=z, x=tx, y=ty)
            for intent in range(3):
                try:
                    raw = urllib.request.urlopen(url, timeout=30).read()
                    break
                except Exception as ex:
                    if intent == 2:
                        raise
            img = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"), dtype=np.float32)
            elev = img[:, :, 0] * 256.0 + img[:, :, 1] + img[:, :, 2] / 256.0 - 32768.0
            mos[(ty - ty0) * 256:(ty - ty0 + 1) * 256, (tx - tx0) * 256:(tx - tx0 + 1) * 256] = elev
    return mos, tx0, ty0


def main():
    latc = (LAT0 + LAT1) / 2.0
    mlon = 111320.0 * math.cos(math.radians(latc))
    nx = int(round((LON1 - LON0) * mlon / DX_M)) + 1
    ny = int(round((LAT1 - LAT0) * 111132.0 / DX_M)) + 1
    dx = (LON1 - LON0) * mlon / (nx - 1)
    dy = (LAT1 - LAT0) * 111132.0 / (ny - 1)
    print("graella: %d x %d (dx=%.0f m, dy=%.0f m)" % (nx, ny, dx, dy))

    mos, tx0, ty0 = baixa_mosaic(ZOOM)

    # mostreig bilineal del mosaic (Web Mercator) a la graella lat/lon regular
    lons = np.linspace(LON0, LON1, nx)
    lats = np.linspace(LAT0, LAT1, ny)          # fila 0 = SUD (com el de Catalunya)
    n = 2 ** ZOOM
    px = ((lons + 180.0) / 360.0 * n - tx0) * 256.0
    r = np.radians(lats)
    py = ((1.0 - np.log(np.tan(r) + 1.0 / np.cos(r)) / math.pi) / 2.0 * n - ty0) * 256.0

    px = np.clip(px, 0, mos.shape[1] - 1.001)
    py = np.clip(py, 0, mos.shape[0] - 1.001)
    X, Y = np.meshgrid(px, py)                  # (ny, nx); Y creix cap al nord? no: py decreix amb lat
    x0 = np.floor(X).astype(int); y0 = np.floor(Y).astype(int)
    fx = X - x0; fy = Y - y0
    H = (mos[y0, x0] * (1 - fx) * (1 - fy) + mos[y0, x0 + 1] * fx * (1 - fy) +
         mos[y0 + 1, x0] * (1 - fx) * fy + mos[y0 + 1, x0 + 1] * fx * fy)

    sea = (H <= 0.0).astype(np.uint8)
    H = np.maximum(H, 0.0).astype(np.float32)
    print("altitud: max %.0f m · mar: %.1f%% de cel·les" % (H.max(), 100.0 * sea.mean()))

    np.savez_compressed("vent_grid.npz", H=H, sea=sea,
                        bbox=np.array([LON0, LON1, LAT0, LAT1], dtype=np.float64),
                        nx=np.int64(nx), ny=np.int64(ny),
                        dx=np.float64(dx), dy=np.float64(dy))
    import os
    print("OK -> vent_grid.npz (%d KB)" % (os.path.getsize("vent_grid.npz") // 1024))


if __name__ == "__main__":
    main()
