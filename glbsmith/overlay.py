"""Lay the model over the product photo and measure where they differ.

This is the reference-image check 3D artists do by hand: put the model in the
same view as the photo, make the photo translucent, and look for parts that
stick out. It finds the view by matching silhouettes, then compares the width
of the two silhouettes band by band from top to bottom. A base drawn three
times too wide shows up as one band with a large difference.

No Blender here: the worker exports triangles, and everything else is numpy
and Pillow, so it can be tested on its own.
"""

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

MASK_SIDE = 384
SEARCH_SIDE = 192
SEARCH_TRIANGLES = 6000
BACKGROUND_TOLERANCE = 6     # per channel, 0-255; low so near-white shades on white stay foreground
BANDS = 12
# A band differs when it is off by this share of the item's height and by this ratio.
BAND_MIN_DIFFERENCE = 0.04
BAND_MAX_RATIO = 1.4
PASS_OVERLAP = 0.6
PANEL_MIN_WIDTH = 270


def photo_mask(image: Image.Image, side: int = MASK_SIDE) -> tuple[np.ndarray | None, Image.Image, str | None]:
    """(foreground mask, resized RGB photo, reason it could not be trusted)."""
    image = image.convert("RGBA")
    image.thumbnail((side, side))
    rgba = np.asarray(image, dtype=np.int16)
    photo = Image.new("RGB", image.size, (255, 255, 255))
    photo.paste(image, mask=image.getchannel("A"))
    if (rgba[..., 3] < 250).mean() > 0.02:
        mask = rgba[..., 3] > 32
    else:
        rgb = rgba[..., :3]
        border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
        background = np.median(border, axis=0)
        close = np.abs(rgb - background).max(axis=-1) <= BACKGROUND_TOLERANCE
        if np.abs(border - background).max(axis=-1).__le__(BACKGROUND_TOLERANCE * 2).mean() < 0.6:
            return None, photo, "The photo's background is not plain, so its outline cannot be measured."
        # Background is what the border can reach; anything enclosed by the item stays in the item.
        reached = np.zeros_like(close)
        reached[0], reached[-1], reached[:, 0], reached[:, -1] = close[0], close[-1], close[:, 0], close[:, -1]
        while True:
            grown = reached.copy()
            grown[1:] |= reached[:-1]
            grown[:-1] |= reached[1:]
            grown[:, 1:] |= reached[:, :-1]
            grown[:, :-1] |= reached[:, 1:]
            grown &= close
            if (grown == reached).all():
                break
            reached = grown
        mask = ~reached
    mask = _main_parts(mask)
    share = mask.mean()
    if share < 0.005 or share > 0.9:
        return None, photo, "The item could not be separated from the photo's background."
    return mask, photo, None


def _main_parts(mask: np.ndarray) -> np.ndarray:
    """Drop specks and captions: keep the largest connected area and others of comparable size."""
    height, width = mask.shape
    labels = np.zeros(mask.shape, dtype=np.int32)
    sizes = [0]
    for start in np.argwhere(mask):
        if labels[tuple(start)]:
            continue
        label = len(sizes)
        stack, count = [tuple(start)], 0
        labels[tuple(start)] = label
        while stack:
            y, x = stack.pop()
            count += 1
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not labels[ny, nx]:
                    labels[ny, nx] = label
                    stack.append((ny, nx))
        sizes.append(count)
    if len(sizes) == 1:
        return mask
    keep = [i for i, size in enumerate(sizes) if i and size >= max(sizes) * 0.1]
    return np.isin(labels, keep)


@dataclass
class View:
    azimuth: float = 0.0     # degrees; 0 = from the front (-Y), 90 = from the item's right (+X)
    elevation: float = 0.0   # degrees above the horizontal
    distance: float = 0.0    # camera distance in item radii; 0 = no perspective


def project(triangles: np.ndarray, view: View) -> tuple[np.ndarray, np.ndarray]:
    """Triangles (n, 3, 3) to image-plane coordinates (x right, y down) and depth (larger is nearer)."""
    low, high = triangles.reshape(-1, 3).min(0), triangles.reshape(-1, 3).max(0)
    centre = (low + high) / 2
    az, el = np.radians(view.azimuth), np.radians(view.elevation)
    to_camera = np.array([np.sin(az) * np.cos(el), -np.cos(az) * np.cos(el), np.sin(el)])
    right = np.array([np.cos(az), np.sin(az), 0.0])
    up = np.cross(to_camera, right)
    points = triangles - centre
    x, y, depth = points @ right, points @ up, points @ to_camera
    if view.distance:
        d = view.distance * max(np.linalg.norm(high - low) / 2, 1e-6)
        factor = d / np.maximum(d - depth, d * 0.1)
        x, y = x * factor, y * factor
    return np.stack([x, -y], axis=-1), depth


def _draw(flat: np.ndarray, shape: tuple[int, int], fills=None) -> Image.Image:
    height, width = shape
    image = Image.new("L" if fills is None else "RGB", (width, height), 0)
    draw = ImageDraw.Draw(image)
    if fills is None:
        for triangle in flat.reshape(-1, 6).tolist():
            draw.polygon(triangle, fill=255)
    else:
        for triangle, fill in zip(flat.reshape(-1, 6).tolist(), fills):
            draw.polygon(triangle, fill=fill)
    return image


def _bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def _thick(mask: np.ndarray) -> np.ndarray:
    # One pixel of slack, so a thin stem one pixel to the side still counts as overlapping.
    return np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(3))) > 0


def _overlap(a: np.ndarray, b: np.ndarray) -> float:
    a, b = _thick(a), _thick(b)
    union = (a | b).sum()
    return float((a & b).sum() / union) if union else 0.0


def _placements(points: np.ndarray, target: np.ndarray):
    """Uniform scales and shifts that line the model up with the photo's outline."""
    low, high = points.reshape(-1, 2).min(0), points.reshape(-1, 2).max(0)
    size = np.maximum(high - low, 1e-6)
    x0, y0, x1, y1 = _bbox(target)
    for scale in {(y1 - y0) / size[1], (x1 - x0) / size[0]}:
        placed = (points - low) * scale
        extent = size * scale
        yield placed + np.array([(x0 + x1 - extent[0]) / 2, (y0 + y1 - extent[1]) / 2]), scale


def _score(triangles, view, target, order=None):
    points, _ = project(triangles if order is None else triangles[order], view)
    best = (-1.0, None, None)
    for placed, scale in _placements(points, target):
        drawn = np.asarray(_draw(placed, target.shape)) > 0
        score = _overlap(drawn, target)
        if score > best[0]:
            best = (score, placed, scale)
    return best


def find_view(triangles: np.ndarray, mask: np.ndarray, azimuth: float | None = None, elevation: float | None = None) -> View:
    """Search for the view that makes the two silhouettes overlap best."""
    side = max(mask.shape)
    small = np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).resize(
        (max(1, mask.shape[1] * SEARCH_SIDE // side), max(1, mask.shape[0] * SEARCH_SIDE // side))), dtype=np.uint8) > 127
    order = None
    if len(triangles) > SEARCH_TRIANGLES:
        areas = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1)
        order = np.argsort(-areas)[:SEARCH_TRIANGLES]

    def search(azimuths, elevations, distances, start=None):
        best = start or (-1.0, View())
        for az in azimuths:
            for el in elevations:
                for distance in distances:
                    view = View(float(az), float(el), float(distance))
                    score = _score(triangles, view, small, order)[0]
                    if score > best[0]:
                        best = (score, view)
        return best

    azimuths = np.arange(-180, 180, 20) if azimuth is None else [azimuth - 20, azimuth - 10, azimuth, azimuth + 10, azimuth + 20]
    elevations = [0, 12, 24, 36] if elevation is None else [max(-10, elevation - 8), elevation, elevation + 8]
    best = search(azimuths, elevations, [0, 4])
    view = best[1]
    best = search([view.azimuth + d for d in (-10, -5, 0, 5, 10)], [max(-10, view.elevation + d) for d in (-6, -3, 0, 3, 6)], [0, 8, 4, 2.5], best)
    return best[1]


def _extents(mask: np.ndarray, top: int, bottom: int) -> list[tuple[float, int]]:
    rows = []
    for band in range(BANDS):
        a = top + (bottom - top) * band // BANDS
        b = max(a + 1, top + (bottom - top) * (band + 1) // BANDS)
        columns = np.nonzero(mask[a:b].any(axis=0))[0]
        rows.append((float(columns.max() - columns.min() + 1) if len(columns) else 0.0, a))
    return rows


def compare(triangles: np.ndarray, colours: np.ndarray, parts: np.ndarray, names: list[str], photo_image: Image.Image,
            *, azimuth: float | None = None, elevation: float | None = None) -> tuple[dict, Image.Image]:
    """(numbers for the agent, picture of the overlay). `height_cm` of the model comes from its own geometry."""
    mask, photo, problem = photo_mask(photo_image)
    if mask is None:
        view = View(azimuth or 0.0, elevation or 0.0)
        return {"verdict": "unreliable", "reason": problem}, _picture(triangles, colours, view, None, photo, None)

    view = find_view(triangles, mask, azimuth, elevation)
    score, placed, scale = _score(triangles, view, mask)
    drawn = np.asarray(_draw(placed, mask.shape)) > 0

    x0, y0, x1, y1 = _bbox(mask)
    mx0, my0, mx1, my1 = _bbox(drawn)
    top, bottom = min(y0, my0), max(y1, my1)
    cm_per_pixel = 100 / scale  # model units are metres
    flagged, bands = [], []
    for index, ((photo_width, a), (model_width, _)) in enumerate(zip(_extents(mask, top, bottom), _extents(drawn, top, bottom))):
        from_bottom = round((bottom - a) / (bottom - top) * 100)
        row = {"band": f"top of band is {from_bottom}% up the item", "photo_cm": round(float(photo_width * cm_per_pixel), 1), "model_cm": round(float(model_width * cm_per_pixel), 1)}
        bands.append(row)
        difference = abs(photo_width - model_width) / max(y1 - y0, 1)
        ratio = (max(photo_width, model_width) + 1) / (min(photo_width, model_width) + 1)
        if difference > BAND_MIN_DIFFERENCE and ratio > BAND_MAX_RATIO:
            flagged.append({**row, "problem": "model is too wide here" if model_width > photo_width else "model is too narrow or missing here"})

    verdict = "pass" if score >= PASS_OVERLAP and not flagged else "fail"
    result = {
        "verdict": verdict,
        "overlap": round(score, 3),
        "pass_needs_overlap": PASS_OVERLAP,
        "view": {"azimuth": view.azimuth, "elevation": view.elevation, "perspective_distance": view.distance},
        "photo_width_per_height": round((x1 - x0) / (y1 - y0), 3),
        "model_width_per_height": round((mx1 - mx0) / (my1 - my0), 3),
        "flagged_bands": flagged,
        "bands_top_to_bottom": bands,
        "note": "cm are approximate, and only compare what this one view can show. Depth seen end-on cannot be checked here.",
    }
    return result, _picture(triangles, colours, view, (placed, scale), photo, mask, drawn)


def _shaded(triangles, colours, view, placed, shape):
    order_points, depth = project(triangles, view)
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-9)
    light = np.array([-0.45, -0.6, 0.66])
    light /= np.linalg.norm(light)
    shade = 0.55 + 0.45 * np.abs(normals @ light)
    fills = np.clip(colours * shade[:, None] * 255, 0, 255).astype(int)
    order = np.argsort(depth.mean(axis=1))  # far to near
    return _draw(placed[order], shape, [tuple(c) for c in fills[order].tolist()])


def _picture(triangles, colours, view, placement, photo, mask, drawn=None) -> Image.Image:
    shape = (photo.height, photo.width)
    if placement is None:
        points, _ = project(triangles, view)
        low, high = points.reshape(-1, 2).min(0), points.reshape(-1, 2).max(0)
        scale = 0.9 * min(shape[1] / max(high[0] - low[0], 1e-6), shape[0] / max(high[1] - low[1], 1e-6))
        placed = (points - (low + high) / 2) * scale + np.array([shape[1] / 2, shape[0] / 2])
    else:
        placed = placement[0]
    model = _shaded(triangles, colours, view, placed, shape)
    occupied = Image.fromarray((np.asarray(model).sum(axis=-1) > 0).astype(np.uint8) * 255)
    on_white = Image.new("RGB", photo.size, (255, 255, 255))
    on_white.paste(model, mask=occupied)
    blend = Image.blend(photo, on_white, 0.5)
    panels = [("photo + model, 50% each", blend)]
    if mask is not None:
        difference = np.full((*shape, 3), 255, np.uint8)
        difference[mask & drawn] = (70, 70, 70)
        difference[drawn & ~mask] = (225, 40, 40)
        difference[mask & ~drawn] = (20, 170, 200)
        panels.append(("red: only the model   blue: only the photo", Image.fromarray(difference)))
    panels.append(("model in the same view", on_white))
    # Tall, narrow photos still need room for their captions.
    cell = max(photo.width, PANEL_MIN_WIDTH)
    sheet = Image.new("RGB", (len(panels) * cell, photo.height + 18), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    for index, (caption, panel) in enumerate(panels):
        sheet.paste(panel, (index * cell + (cell - photo.width) // 2, 18))
        draw.text((index * cell + 4, 3), caption, fill=(0, 0, 0))
    return sheet
