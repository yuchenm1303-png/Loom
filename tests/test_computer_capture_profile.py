"""Screenshot encoding is the dominant cost of a Computer Use step.

Measured on one real trace: provider latency fit ``4.6s + bytes * 4.94e-6``
across two orders of magnitude, so a 4.4 MB lossless capture of a 2560x1600
desktop spent ~22 seconds on upload alone while the model tokenized it down to
~4000 image tokens regardless. Lossless capture buys nothing a grounding model
can use.
"""

from __future__ import annotations

import io

import pytest

from app.agent_runtime.computer_windows import CAPTURE_PROFILES, DEFAULT_CAPTURE_PROFILE

PIL = pytest.importorskip("PIL.Image")


class _Encoder:
    """The operator's encoder and profile switch, without a Windows desktop.

    PyWinAutoWindowsOperator.__init__ requires pyautogui and pywinauto, which
    live in Loom's own runtime environment rather than the test one, so the two
    methods under test are bound directly onto a stub holding the state they use.
    """

    def __init__(self, profile: str = DEFAULT_CAPTURE_PROFILE) -> None:
        import threading

        from app.agent_runtime.computer_windows import PyWinAutoWindowsOperator

        self.capture_profile = profile
        self._lock = threading.RLock()
        self._encode = PyWinAutoWindowsOperator._encode.__get__(self)
        self.set_capture_profile = PyWinAutoWindowsOperator.set_capture_profile.__get__(self)


def test_the_settings_page_values_are_all_real_profiles():
    """The three options the desktop offers must all be selectable."""

    encoder = _Encoder()
    for quality in ("fast", "balanced", "high"):
        assert encoder.set_capture_profile(quality) == quality
        assert encoder.capture_profile == quality


def test_an_unknown_profile_is_refused_and_leaves_the_previous_one():
    encoder = _Encoder("balanced")
    with pytest.raises(ValueError, match="unknown computer capture profile"):
        encoder.set_capture_profile("ultra")
    assert encoder.capture_profile == "balanced"


def _desktop_image():
    """A 2560x1600 frame shaped like real application chrome.

    Photographic or noisy fixtures would invert the result being measured: JPEG
    wins on desktop UI precisely because it is mostly flat panels with small
    high-contrast text, which is what this draws.
    """

    from PIL import Image, ImageDraw

    image = Image.new("RGB", (2560, 1600), (243, 243, 247))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, 2560, 96], fill=(32, 36, 48))
    draw.rectangle([0, 1520, 2560, 1600], fill=(28, 30, 40))
    draw.rectangle([64, 160, 780, 1460], fill=(255, 255, 255), outline=(210, 212, 220))
    draw.rectangle([840, 160, 2496, 1460], fill=(255, 255, 255), outline=(210, 212, 220))
    for row in range(200, 1440, 34):
        # Text-like runs: short dark bars of varying length on a light panel.
        draw.rectangle([96, row, 96 + 180 + (row % 420), row + 14], fill=(64, 68, 82))
        draw.rectangle([880, row, 880 + 300 + (row % 900), row + 14], fill=(48, 52, 64))
    draw.rectangle([2180, 1360, 2460, 1424], fill=(52, 120, 246))
    return image


def test_default_profile_does_not_upload_a_lossless_desktop():
    """The default must be lossy and bounded, not PNG at native resolution.

    No byte-ratio is asserted against this fixture. Synthetic bitmaps do not
    compress like real desktops in either direction -- flat rectangles let PNG
    win absurdly, procedural noise lets JPEG lose absurdly -- so a ratio here
    would measure the fixture rather than the encoder. The size evidence comes
    from the production trace quoted in this module's docstring.
    """

    encoded, media_type, details = _Encoder(DEFAULT_CAPTURE_PROFILE)._encode(_desktop_image())

    assert media_type == "image/jpeg"
    assert encoded[:2] == b"\xff\xd8"
    assert details["downscale"] < 1.0
    assert details["image_width"] * details["image_height"] <= CAPTURE_PROFILES[
        DEFAULT_CAPTURE_PROFILE
    ]["max_pixels"]


def test_every_profile_round_trips_through_pillow():
    from PIL import Image

    image = _desktop_image()
    for name in CAPTURE_PROFILES:
        encoded, media_type, details = _Encoder(name)._encode(image)
        decoded = Image.open(io.BytesIO(encoded))
        assert decoded.width == details["image_width"]
        assert decoded.height == details["image_height"]
        assert decoded.format == ("JPEG" if media_type == "image/jpeg" else "PNG")


def test_downscaling_preserves_aspect_ratio():
    """Model points are normalized against the frame, so the image must not skew."""

    image = _desktop_image()
    _, _, details = _Encoder("fast")._encode(image)
    source = image.width / image.height
    encoded = details["image_width"] / details["image_height"]
    assert abs(source - encoded) < 0.01


def test_a_small_window_is_never_upscaled():
    from PIL import Image

    small = Image.new("RGB", (442, 581), (10, 10, 10))
    _, _, details = _Encoder("high")._encode(small)
    assert details["image_width"] == 442
    assert details["image_height"] == 581
    assert details["downscale"] == 1.0


def test_lossless_remains_available_as_an_escape_hatch():
    encoded, media_type, _ = _Encoder("lossless")._encode(_desktop_image())
    assert media_type == "image/png"
    assert encoded[:8] == b"\x89PNG\r\n\x1a\n"
