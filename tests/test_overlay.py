"""The photo check, on synthetic models and photos. No Blender and no agent."""

import numpy as np
import pytest
from PIL import Image, ImageDraw

from glbsmith.overlay import View, compare, photo_mask, project


def box(low, high):
    (x0, y0, z0), (x1, y1, z1) = low, high
    corners = np.array([[x, y, z] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)], dtype=float)
    faces = [(0, 1, 3, 2), (4, 5, 7, 6), (0, 1, 5, 4), (2, 3, 7, 6), (0, 2, 6, 4), (1, 3, 7, 5)]
    return np.array([[corners[a], corners[b], corners[c]] for f in faces for a, b, c in ((f[0], f[1], f[2]), (f[0], f[2], f[3]))])


def lamp(base_width):
    """A wide flat base, a thin post and a shade at the top, one metre tall."""
    half = base_width / 2
    return np.concatenate([
        box((-half, -half, 0), (half, half, 0.04)),
        box((-0.01, -0.01, 0.04), (0.01, 0.01, 0.8)),
        box((-0.12, -0.12, 0.8), (0.12, 0.12, 1.0)),
    ])


def lamp_photo(size=400):
    """Front view of lamp(0.3) on white, with a little margin."""
    image = Image.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    pixels = size * 0.8  # per metre
    middle, floor = size / 2, size * 0.9
    draw.rectangle((middle - 0.15 * pixels, floor - 0.04 * pixels, middle + 0.15 * pixels, floor), fill=(20, 20, 20))
    draw.rectangle((middle - 0.01 * pixels, floor - 0.8 * pixels, middle + 0.01 * pixels, floor), fill=(20, 20, 20))
    draw.rectangle((middle - 0.12 * pixels, floor - pixels, middle + 0.12 * pixels, floor - 0.8 * pixels), fill=(235, 230, 215))
    return image


def run(triangles, photo, **view):
    colours = np.full((len(triangles), 3), 0.5)
    return compare(triangles, colours, np.zeros(len(triangles), dtype=int), ["lamp"], photo, **view)


def test_up_is_up_and_right_is_right():
    # Up: a point high on the item comes out higher in the image (smaller y) from every side.
    triangles = np.array([[[0, 0, 0], [0, 0, 1], [1, 0, 0]]], dtype=float)
    for azimuth in (0, 90, 180, -90, 35):
        points, _ = project(triangles, View(azimuth=azimuth, elevation=20))
        assert points[0, 1, 1] < points[0, 0, 1]
    # From the front the item's +X is on the right of the picture, from behind on the left.
    assert project(triangles, View(azimuth=0))[0][0, 2, 0] > project(triangles, View(azimuth=0))[0][0, 0, 0]
    assert project(triangles, View(azimuth=180))[0][0, 2, 0] < project(triangles, View(azimuth=180))[0][0, 0, 0]


def test_azimuth_moves_the_camera_as_documented():
    # A point on the item's front (-Y) is nearest from azimuth 0, and +X is nearest from azimuth 90.
    triangles = np.array([[[0, -1, 0], [1, 0, 0], [0, 0, 1]]], dtype=float)
    _, front = project(triangles, View(azimuth=0))
    _, right = project(triangles, View(azimuth=90))
    assert front[0, 0] > front[0, 1]
    assert right[0, 1] > right[0, 0]


def test_a_correct_model_passes():
    result, picture = run(lamp(0.3), lamp_photo(), azimuth=0, elevation=0)
    assert result["verdict"] == "pass", result
    assert result["overlap"] > 0.8
    assert picture.width > picture.height


def test_a_narrow_photo_still_has_room_for_captions():
    photo = lamp_photo().crop((120, 0, 280, 400))
    _, picture = run(lamp(0.3), photo, azimuth=0, elevation=0)
    assert picture.width == 3 * 270


def test_a_stretched_base_fails_and_says_where():
    result, _ = run(lamp(1.0), lamp_photo(), azimuth=0, elevation=0)
    assert result["verdict"] == "fail"
    assert result["flagged_bands"]
    assert any("too wide" in band["problem"] for band in result["flagged_bands"])
    assert result["model_width_per_height"] > result["photo_width_per_height"] * 2


def test_a_missing_part_fails():
    no_shade = np.concatenate([box((-0.15, -0.15, 0), (0.15, 0.15, 0.04)), box((-0.01, -0.01, 0.04), (0.01, 0.01, 1.0))])
    result, _ = run(no_shade, lamp_photo(), azimuth=0, elevation=0)
    assert result["verdict"] == "fail"
    assert any("narrow or missing" in band["problem"] for band in result["flagged_bands"])


def test_full_search_finds_an_angled_view():
    triangles = lamp(0.3)
    points, _ = project(triangles, View(azimuth=-40, elevation=10))
    points = (points - points.reshape(-1, 2).min(0)) * 300 + 40
    photo = Image.new("RGB", (400, 400), (255, 255, 255))
    draw = ImageDraw.Draw(photo)
    for triangle in points.reshape(-1, 6).tolist():
        draw.polygon(triangle, fill=(30, 30, 30))
    result, _ = run(triangles, photo)
    assert result["verdict"] == "pass", result


def test_near_white_item_on_white_stays_foreground():
    mask, _, problem = photo_mask(lamp_photo())
    assert problem is None
    assert mask[int(400 * 0.9 - 0.9 * 320), 200]  # inside the shade, which is nearly the background colour


def test_a_caption_is_ignored():
    photo = lamp_photo()
    ImageDraw.Draw(photo).text((10, 385), "free shipping", fill=(0, 0, 0))
    mask, _, _ = photo_mask(photo)
    assert not mask[380:, :60].any()


@pytest.mark.parametrize("noisy", [True, False])
def test_a_busy_or_blank_photo_is_reported_not_guessed(noisy):
    data = np.random.default_rng(1).integers(0, 255, (200, 200, 3), dtype=np.uint8) if noisy else np.full((200, 200, 3), 255, np.uint8)
    result, picture = run(lamp(0.3), Image.fromarray(data))
    assert result["verdict"] == "unreliable" and result["reason"]
    assert picture.width > 0


def test_a_cut_out_uses_its_alpha():
    photo = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(photo).rectangle((60, 20, 140, 180), fill=(255, 255, 255, 255))  # white item, on transparent
    mask, _, problem = photo_mask(photo)
    assert problem is None and mask[100, 100] and not mask[100, 10]
