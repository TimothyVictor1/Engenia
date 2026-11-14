"""Render build/mechanical STL files as an exploded isometric view (README images).

Usage: python docs/images/render_stl.py <project> <out.png> <lid-lift-mm>
Needs numpy and Pillow. Not part of the pipeline.
"""

import os
import struct
import sys

import numpy as np
from PIL import Image, ImageChops


def load(p):
    b = open(p, "rb").read()
    n = struct.unpack("<I", b[80:84])[0]
    a = np.frombuffer(
        b[84 : 84 + 50 * n], dtype=np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")])
    )
    return a["v"].reshape(-1, 3, 3).astype(float)


def render(parts, out, size=1000, az=35, el=32, ss=2):
    allv = np.concatenate([t.reshape(-1, 3) for t, _ in parts])
    c = (allv.min(0) + allv.max(0)) / 2
    a, e = np.radians(az), np.radians(el)
    Rz = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
    t = e - np.pi / 2
    Rx = np.array([[1, 0, 0], [0, np.cos(t), -np.sin(t)], [0, np.sin(t), np.cos(t)]])
    R = Rx @ Rz
    tv = [((tr - c) @ R.T, col) for tr, col in parts]
    allp = np.concatenate([v.reshape(-1, 3) for v, _ in tv])
    scale = size * ss * 0.9 / max(np.ptp(allp[:, 0]), np.ptp(allp[:, 1]))
    W = size * ss
    img = np.full((W, W, 3), 255, np.uint8)
    zbuf = np.full((W, W), -1e18)
    light = np.array([0.35, -0.45, 0.82])
    light /= np.linalg.norm(light)
    for v, col in tv:
        nrm = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
        ln = np.linalg.norm(nrm, axis=1)
        ln[ln == 0] = 1
        nrm /= ln[:, None]
        shade = np.abs(nrm @ light) * 0.65 + 0.35
        px = v[:, :, 0] * scale + W / 2
        py = W / 2 - v[:, :, 1] * scale
        pz = v[:, :, 2]
        for i in range(len(v)):
            x, y, z = px[i], py[i], pz[i]
            x0, x1 = int(max(np.floor(x.min()), 0)), int(min(np.ceil(x.max()), W - 1))
            y0, y1 = int(max(np.floor(y.min()), 0)), int(min(np.ceil(y.max()), W - 1))
            if x1 < x0 or y1 < y0:
                continue
            den = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
            if abs(den) < 1e-9:
                continue
            gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
            l0 = ((y[1] - y[2]) * (gx - x[2]) + (x[2] - x[1]) * (gy - y[2])) / den
            l1 = ((y[2] - y[0]) * (gx - x[2]) + (x[0] - x[2]) * (gy - y[2])) / den
            l2 = 1 - l0 - l1
            m = (l0 >= -1e-6) & (l1 >= -1e-6) & (l2 >= -1e-6)
            if not m.any():
                continue
            zz = l0 * z[0] + l1 * z[1] + l2 * z[2]
            sub = zbuf[y0 : y1 + 1, x0 : x1 + 1]
            upd = m & (zz > sub)
            sub[upd] = zz[upd]
            img[y0 : y1 + 1, x0 : x1 + 1][upd] = [int(ch * shade[i]) for ch in col]
    im = Image.fromarray(img).resize((size, size), Image.LANCZOS)
    bbox = ImageChops.difference(im, Image.new("RGB", im.size, (255, 255, 255))).getbbox()
    pad = 20
    im.crop(
        (
            max(bbox[0] - pad, 0),
            max(bbox[1] - pad, 0),
            min(bbox[2] + pad, size),
            min(bbox[3] + pad, size),
        )
    ).save(out, optimize=True)


proj, out, lift = sys.argv[1], sys.argv[2], float(sys.argv[3])
base = f"projects/{proj}/build/mechanical/"
body = base + ("hull.stl" if os.path.exists(base + "hull.stl") else "body.stl")
hull = load(body)
lid = load(base + "lid.stl").copy()
lid[:, :, 2] += lift
render([(hull, (70, 130, 180)), (lid, (230, 160, 60))], out)
