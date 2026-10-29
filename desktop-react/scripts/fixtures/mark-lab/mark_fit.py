"""Loom's mark as geometry: fit a vector model to the original's silhouette, export it, score what was exported.

    python mark_fit.py fit [steps]   hill-climb model.json against loom-mark-512.png (alpha >= .8), save it
    python mark_fit.py export        write ../../../src/components/loomMarkGeometry.ts from model.json
    python mark_fit.py score         rasterise the exported geometry and print its overlap with the original

Needs numpy and Pillow. The model: the sphere (cx, cy, r), two ribbons (centreline control points `p` with a width
`w` at each, through which a Catmull-Rom spline runs) and the arrowhead polygon (tip, right barb, notch, lower barb),
all in the original's 512 x 512 space. The overlap is IoU of the filled shapes against the solid silhouette.
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ORIGINAL = HERE / "loom-mark-512.png"
MODEL = HERE / "model.json"
GEOMETRY = HERE.parents[2] / "src" / "components" / "loomMarkGeometry.ts"
N = 4  # spline samples per segment in the exported paths


def target(size=512):
    alpha = Image.open(ORIGINAL).convert("RGBA").getchannel("A")
    solid = Image.fromarray(((np.asarray(alpha) >= 204) * 255).astype(np.uint8))
    return np.asarray(solid.resize((size, size), Image.BOX)) >= 128


def catmull(points, n):
    p = np.asarray(points, float)
    p = np.vstack([2 * p[0] - p[1], p, 2 * p[-1] - p[-2]])
    out = []
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        for t in np.linspace(0, 1, n, endpoint=False):
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    out.append(p[-2])
    return np.asarray(out)


def spline(rb, n):
    c = catmull(rb["p"], n)
    w = np.interp(np.linspace(0, len(rb["w"]) - 1, len(c)), np.arange(len(rb["w"])), rb["w"])
    return c, w


def sides(c, w):
    d = np.gradient(c, axis=0)
    d /= np.linalg.norm(d, axis=1, keepdims=True) + 1e-9
    n = np.stack([-d[:, 1], d[:, 0]], 1)
    return c + n * (w[:, None] / 2), c - n * (w[:, None] / 2)


def render(m, size):
    k = size / 512
    im = Image.new("L", (size, size), 0)
    dr = ImageDraw.Draw(im)
    cx, cy, r = m["sphere"]
    dr.ellipse([(cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k], fill=255)
    for rb in m["ribbons"]:
        left, right = sides(*spline(rb, 24))
        dr.polygon([tuple(q * k) for q in np.vstack([left, right[::-1]])], fill=255)
    dr.polygon([(x * k, y * k) for x, y in m["arrow"]], fill=255)
    return np.asarray(im) > 127


def iou(a, b):
    return float((a & b).sum() / (a | b).sum())


def fit(steps):
    m = json.loads(MODEL.read_text())
    shapes = [(len(rb["p"]), rb) for rb in m["ribbons"]]

    def flat(m):
        v = list(m["sphere"])
        for rb in m["ribbons"]:
            v += [c for p in rb["p"] for c in p] + list(rb["w"])
        return np.asarray(v + [c for p in m["arrow"] for c in p], float)

    def unflat(v):
        out, i = {"sphere": list(v[:3]), "ribbons": []}, 3
        for n, rb in shapes:
            p = v[i:i + 2 * n].reshape(n, 2).tolist(); i += 2 * n
            w = np.maximum(v[i:i + n], 2).tolist(); i += n
            out["ribbons"].append({"name": rb["name"], "p": p, "w": w})
        out["arrow"] = v[i:].reshape(-1, 2).tolist()
        return out

    rng = np.random.default_rng(1)
    t = target(128)
    v = flat(m)
    best = iou(render(unflat(v), 128), t)
    for step in range(steps):
        cand = v.copy()
        idx = rng.choice(len(v), size=rng.integers(1, 3), replace=False)
        cand[idx] += rng.normal(0, 4 if step < steps * .6 else 1.5, len(idx))
        s = iou(render(unflat(cand), 128), t)
        if s >= best:
            v, best = cand, s
    MODEL.write_text(json.dumps(unflat(v), indent=1))
    print("IoU@512", round(iou(render(unflat(v), 512), target()), 4))


def num(v):
    return str(int(round(v)))


def bez(points, move=True):
    """A Catmull-Rom curve through the points, as cubic Beziers."""
    p = np.asarray(points, float)
    ext = np.vstack([2 * p[0] - p[1], p, 2 * p[-1] - p[-2]])
    d = f"{'M' if move else 'L'}{num(p[0][0])} {num(p[0][1])}"
    for i in range(1, len(p)):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        c1, c2 = p1 + (p2 - p0) / 6, p2 - (p3 - p1) / 6
        d += f"C{num(c1[0])} {num(c1[1])} {num(c2[0])} {num(c2[1])} {num(p2[0])} {num(p2[1])}"
    return d


def export():
    m = json.loads(MODEL.read_text())
    upper, lower = m["ribbons"]
    cu, wu = spline(upper, N)
    cl, wl = spline(lower, N)

    def band(c, w):
        left, right = sides(c, w)
        return bez(left) + bez(right[::-1], move=False) + "Z"

    def edges(c, w):
        left, right = sides(c, w)
        return bez(left) + bez(right)

    # The upper ribbon is in front of the sphere from the first control point (after its top) that reaches the sphere.
    cx, cy, r = m["sphere"]
    split = N * next(i for i, (x, y) in enumerate(upper["p"]) if i > 4 and np.hypot(x - cx, y - cy) < r + 25)
    # The light's route: the upper ribbon from its tail round to where it meets the lower ribbon (the lower ribbon's
    # nearest control point to the upper ribbon's end), then on along the lower ribbon into the arrowhead's middle.
    end = np.asarray(upper["p"][-1])
    join = int(np.argmin([np.hypot(*(np.asarray(p) - end)) for p in lower["p"]]))
    arrow = np.asarray(m["arrow"])
    route = bez(np.asarray(upper["p"] + lower["p"][join + 1:] + [((arrow[0] + arrow[1] + arrow[3]) / 3).tolist()]))
    direction = (arrow[0] - arrow[2]) / np.linalg.norm(arrow[0] - arrow[2])
    overlap = iou(render(m, 512), target())

    GEOMETRY.write_text(f'''// Generated by scripts/fixtures/mark-lab/mark_fit.py from Loom's 512px mark (loom-mark-512.png beside it), whose
// silhouette the model in model.json was fitted to (overlap {overlap:.2f}). Coordinates are the original's own 512 x 512
// space. Do not edit by hand: refit or adjust model.json, then `python mark_fit.py export`.

export const MARK_VIEWBOX = "0 0 512 512";

/** The glass sphere. */
export const SPHERE = {{ cx: {num(cx)}, cy: {num(cy)}, r: {num(r)} }};

/** The upper ribbon from its tail (behind the sphere, top right) over the top and down the left side. */
export const RIBBON_UPPER_BACK = "{band(cu[:split + 1], wu[:split + 1])}";

/** The upper ribbon where it crosses in front of the sphere, down to the bottom band. */
export const RIBBON_UPPER_FRONT = "{band(cu[split:], wu[split:])}";

/** The lower ribbon: round the bottom left, along the bottom and up the right side into the arrowhead. */
export const RIBBON_LOWER = "{band(cl, wl)}";

/** The ribbons' two sides as open lines (no ends), for their lit glass edges. */
export const EDGES_UPPER_BACK = "{edges(cu[:split + 1], wu[:split + 1])}";
export const EDGES_UPPER_FRONT = "{edges(cu[split:], wu[split:])}";
export const EDGES_LOWER = "{edges(cl, wl)}";

/** The arrowhead: tip, right barb, notch, lower barb. */
export const ARROW = "M{'L'.join(f'{num(x)} {num(y)}' for x, y in arrow)}Z";

/** Unit vector the arrowhead points along (notch to tip). */
export const ARROW_DIRECTION = {{ x: {direction[0]:.3f}, y: {direction[1]:.3f} }};

/** The ribbon's centreline from tail to arrowhead: the route the light travels. */
export const ROUTE = "{route}";
''', encoding="utf-8")


def polygons(d):
    """Flatten an exported path (M, L, C, Z) into polygons."""
    out, cur, pos = [], [], None
    for cmd, args in re.findall(r"([MLCZ])([^MLCZ]*)", d):
        n = list(map(float, re.findall(r"-?[\d.]+", args)))
        if cmd == "M":
            if cur:
                out.append(cur)
            pos = (n[0], n[1]); cur = [pos]
        elif cmd == "L":
            for i in range(0, len(n), 2):
                pos = (n[i], n[i + 1]); cur.append(pos)
        elif cmd == "C":
            for i in range(0, len(n), 6):
                p0, c1, c2, p3 = map(np.array, (pos, n[i:i + 2], n[i + 2:i + 4], n[i + 4:i + 6]))
                for t in np.linspace(0, 1, 12)[1:]:
                    cur.append(tuple((1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * c1 + 3 * (1 - t) * t * t * c2 + t ** 3 * p3))
                pos = tuple(p3)
    if cur:
        out.append(cur)
    return out


def score():
    src = GEOMETRY.read_text(encoding="utf-8")
    paths = dict(re.findall(r'export const (\w+) = "([^"]*)"', src))
    cx, cy, r = map(float, re.search(r"SPHERE = \{ cx: ([\d.]+), cy: ([\d.]+), r: ([\d.]+) \}", src).groups())
    alpha = np.asarray(Image.open(ORIGINAL).convert("RGBA").getchannel("A"))
    solid = Image.fromarray(((alpha >= 204) * 255).astype(np.uint8))
    for size in (512, 64, 22):
        k = size * 4 / 512
        im = Image.new("L", (size * 4, size * 4), 0)
        dr = ImageDraw.Draw(im)
        for name in ("RIBBON_UPPER_BACK", "RIBBON_UPPER_FRONT", "RIBBON_LOWER", "ARROW"):
            for p in polygons(paths[name]):
                dr.polygon([(x * k, y * k) for x, y in p], fill=255)
        dr.ellipse([(cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k], fill=255)
        mine = np.asarray(im.resize((size, size), Image.BOX)).astype(float)
        theirs = np.asarray(solid.resize((size, size), Image.BOX)).astype(float)
        # Coverage overlap, sum(min) / sum(max): no threshold, so a 22px grid is not judged by rounding.
        print(f"{size}px overlap {np.minimum(mine, theirs).sum() / np.maximum(mine, theirs).sum():.3f}")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "score"
    if command == "fit":
        fit(int(sys.argv[2]) if len(sys.argv) > 2 else 4000)
    elif command == "export":
        export()
    else:
        score()
