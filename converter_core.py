"""
TIFF / BMP -> 8-bit grayscale converter core engine.
Shared between Desktop (Tkinter) and Web (Pyodide WebAssembly).

Supports:
- 8-bit, 16-bit, and 32-bit int/float RGB, RGBA, CMYK, and planar TIFFs.
- BMP files.
- Alpha compositing: white, black, custom color, or ignore.
- 16-bit / 32-bit scaling (min-max) or truncation (high bits).
- Embedded ICC color profile support (matrix/TRC RGB profiles and gray profiles)
  with colorimetric luminance calculation.
- Multi-page TIFF input and output (LZW compressed).
- Pure in-memory and Pyodide WebAssembly compatibility.
"""

import base64
import io
import os
from pathlib import Path
import tempfile

import numpy as np
from PIL import Image, ImageSequence

try:
    import tifffile
except ImportError:
    tifffile = None

Image.MAX_IMAGE_PIXELS = None  # large scan/laser images expected

VERSION = "PWA V1.0 (Core V2.1)"
SUPPORTED_EXT = {".tif", ".tiff", ".bmp"}

ALPHA_WHITE = "white"
ALPHA_BLACK = "black"
ALPHA_IGNORE = "ignore"
ALPHA_CUSTOM = "custom"

ICC_IGNORE = "ignore"     # plain luma on stored values
ICC_USE = "use"           # use embedded matrix/TRC or gray profile

HI_SCALE = "scale"        # map source range to 0-255
HI_TRUNCATE = "truncate"  # keep high bits (16-bit: value >> 8)


# --------------------------------------------------------------------------
# Metadata & Image Inspection
# --------------------------------------------------------------------------

def bit_depth_of(mode: str) -> str:
    return {
        "1": "1-bit", "L": "8-bit", "LA": "8-bit + alpha", "La": "8-bit + alpha",
        "P": "8-bit palette", "PA": "8-bit palette + alpha",
        "RGB": "24-bit (8/ch)", "RGBA": "32-bit (8/ch)", "RGBX": "32-bit (8/ch)",
        "CMYK": "32-bit (8/ch)", "YCbCr": "24-bit (8/ch)", "LAB": "24-bit (8/ch)",
        "HSV": "24-bit (8/ch)", "I": "32-bit int", "F": "32-bit float",
        "I;16": "16-bit", "I;16L": "16-bit", "I;16B": "16-bit", "I;16N": "16-bit",
    }.get(mode, mode)


def _is_tiff_name(path: str) -> bool:
    return Path(path).suffix.lower() in (".tif", ".tiff")


def _describe_tifffile(path: str) -> dict:
    with tifffile.TiffFile(path) as tf:
        pages = [p for p in tf.pages if not getattr(p, "is_reduced", False)] or list(tf.pages)
        p = pages[0]
        s = p.samplesperpixel
        kind = p.dtype.kind
        if kind == "f":
            bits = f"{p.dtype.itemsize * 8}-bit float"
        else:
            bits = f"{p.dtype.itemsize * 8}-bit"
        photo = p.photometric.name if p.photometric is not None else ""
        if photo == "SEPARATED":
            chan = "CMYK" if s == 4 else f"CMYK+{s - 4}"
        elif s == 1:
            chan = "L"
        elif s == 2:
            chan = "LA"
        elif s == 3:
            chan = "RGB"
        elif s == 4:
            chan = "RGBA"
        else:
            chan = f"{s}ch"
        depth = f"{bits}/ch" + (" + alpha" if chan in ("LA", "RGBA") else "")
        return {
            "name": os.path.basename(path), "format": "TIFF",
            "mode": f"{chan};{p.dtype.itemsize * 8}{'F' if kind == 'f' else ''}",
            "depth": depth, "size": f"{p.imagewidth} x {p.imagelength}",
            "width": int(p.imagewidth), "height": int(p.imagelength),
            "frames": len(pages),
        }


def describe_image(path: str) -> dict:
    tf_err = None
    if tifffile is not None and _is_tiff_name(path):
        try:
            return _describe_tifffile(path)
        except Exception as e:
            tf_err = e
    try:
        with Image.open(path) as im:
            return {
                "name": os.path.basename(path),
                "format": im.format or "?",
                "mode": im.mode,
                "depth": bit_depth_of(im.mode),
                "size": f"{im.width} x {im.height}",
                "width": int(im.width), "height": int(im.height),
                "frames": getattr(im, "n_frames", 1),
            }
    except Exception as e:
        hint = ""
        if _is_tiff_name(path):
            hint = ("" if tifffile is not None else
                    " (tifffile not installed: pip install tifffile imagecodecs)")
            if tf_err is not None:
                hint = f" (tifffile: {tf_err})"
        raise RuntimeError(f"{e}{hint}") from e


def _parse_color(color):
    """Accept (r,g,b) tuple or '#rrggbb'. Returns (r,g,b)."""
    if isinstance(color, str):
        c = color.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    return tuple(int(v) for v in color[:3])


def _luma(rgb) -> int:
    r, g, b = rgb
    return int(round(0.299 * r + 0.587 * g + 0.114 * b))


def _background_rgb(alpha_mode, custom_color):
    if alpha_mode == ALPHA_BLACK:
        return (0, 0, 0)
    if alpha_mode == ALPHA_CUSTOM:
        return _parse_color(custom_color)
    return (255, 255, 255)


def _array_to_u8(arr: np.ndarray, hi_mode: str) -> Image.Image:
    """Reduce an int/float array to 8-bit according to hi_mode."""
    if hi_mode == HI_SCALE:
        a = arr.astype(np.float64)
        lo, hi = float(a.min()), float(a.max())
        if hi > lo:
            out = (a - lo) * (255.0 / (hi - lo))
        else:
            out = np.clip(a, 0, 255)
        out = np.rint(out)
    else:  # truncate
        if np.issubdtype(arr.dtype, np.floating):
            out = np.floor(arr)
        else:
            a = arr.astype(np.int64)
            peak = int(a.max()) if a.size else 0
            if peak <= 255:
                shift = 0
            elif peak <= 65535:
                shift = 8
            else:
                shift = 24
            out = a >> shift
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "L")


# --------------------------------------------------------------------------
# ICC Profile Support (matrix/TRC RGB profiles and gray TRC profiles)
# --------------------------------------------------------------------------

_LUT_N = 1 << 16
_LUT_X = np.linspace(0.0, 1.0, _LUT_N)


def _srgb_encode(lin):
    lin = np.clip(lin, 0.0, 1.0)
    return np.where(lin <= 0.0031308, lin * 12.92,
                    1.055 * np.power(lin, 1.0 / 2.4) - 0.055)


def _u32(b, o):
    return int.from_bytes(b[o:o + 4], "big")


def _u16(b, o):
    return int.from_bytes(b[o:o + 2], "big")


def _s15(b, o):
    return int.from_bytes(b[o:o + 4], "big", signed=True) / 65536.0


def _read_curve(data: bytes, off: int) -> np.ndarray:
    """Return the decode LUT (encoded 0..1 -> linear 0..1) sampled on _LUT_X."""
    sig = data[off:off + 4]
    if sig == b"curv":
        n = _u32(data, off + 8)
        if n == 0:
            return _LUT_X.copy()
        if n == 1:
            return np.power(_LUT_X, _u16(data, off + 12) / 256.0)
        vals = np.frombuffer(data[off + 12: off + 12 + 2 * n], dtype=">u2").astype(np.float64) / 65535.0
        return np.interp(_LUT_X, np.linspace(0.0, 1.0, n), vals)
    if sig == b"para":
        ft = _u16(data, off + 8)
        counts = {0: 1, 1: 3, 2: 4, 3: 5, 4: 7}
        if ft not in counts:
            raise ValueError("unknown parametric curve type")
        p = [_s15(data, off + 12 + 4 * i) for i in range(counts[ft])]
        x, g = _LUT_X, p[0]
        with np.errstate(all="ignore"):
            if ft == 0:
                y = np.power(x, g)
            elif ft == 1:
                a, b = p[1], p[2]
                y = np.where(x >= -b / a, np.power(np.maximum(a * x + b, 0), g), 0.0)
            elif ft == 2:
                a, b, c = p[1], p[2], p[3]
                y = np.where(x >= -b / a, np.power(np.maximum(a * x + b, 0), g) + c, c)
            elif ft == 3:
                a, b, c, d = p[1], p[2], p[3], p[4]
                y = np.where(x >= d, np.power(np.maximum(a * x + b, 0), g), c * x)
            else:
                a, b, c, d, e, f = p[1:7]
                y = np.where(x >= d, np.power(np.maximum(a * x + b, 0), g) + e, c * x + f)
        return np.clip(y, 0.0, 1.0)
    raise ValueError("unsupported tone curve type")


def _icc_name(data: bytes, tags: dict) -> str:
    try:
        off, size = tags[b"desc"]
        sig = data[off:off + 4]
        if sig == b"desc":
            n = _u32(data, off + 8)
            return data[off + 12: off + 12 + max(n - 1, 0)].decode("latin-1").strip() or "ICC profile"
        if sig == b"mluc":
            ln, so = _u32(data, off + 20), _u32(data, off + 24)
            return data[off + so: off + so + ln].decode("utf-16-be").strip() or "ICC profile"
    except Exception:
        pass
    return "ICC profile"


class IccProfile:
    """Gray-conversion model of a matrix/TRC RGB or gray TRC ICC profile."""

    def __init__(self, kind, luts, ycoef, name):
        self.kind = kind          # "rgb" or "gray"
        self.luts = luts          # list of decode LUTs (3 for rgb, 1 for gray)
        self.ycoef = ycoef        # luminance weights (rgb only), sum = 1
        self.name = name

    def to_gray(self, enc: np.ndarray) -> np.ndarray:
        """enc: (H,W,3) for rgb kind or (H,W) for gray kind, encoded 0..1.
        Returns sRGB-encoded luminance 0..1."""
        if self.kind == "rgb":
            lin = sum(self.ycoef[i] * np.interp(enc[..., i], _LUT_X, self.luts[i])
                      for i in range(3))
        else:
            lin = np.interp(enc, _LUT_X, self.luts[0])
        return _srgb_encode(lin)


def parse_icc(data):
    """Return (IccProfile or None, name-or-reason)."""
    try:
        data = bytes(data)
        if len(data) < 132 or data[36:40] != b"acsp":
            raise ValueError("not a valid ICC profile")
        space = data[16:20]
        tags = {}
        for i in range(_u32(data, 128)):
            o = 132 + 12 * i
            tags[data[o:o + 4]] = (_u32(data, o + 4), _u32(data, o + 8))
        name = _icc_name(data, tags)
        if space == b"RGB ":
            need = (b"rXYZ", b"gXYZ", b"bXYZ", b"rTRC", b"gTRC", b"bTRC")
            if any(t not in tags for t in need):
                raise ValueError("LUT-based RGB profile (no matrix/TRC)")
            y = [_s15(data, tags[t][0] + 12) for t in need[:3]]
            s = sum(y)
            if s <= 0:
                raise ValueError("invalid colorant data")
            luts = [_read_curve(data, tags[t][0]) for t in need[3:]]
            return IccProfile("rgb", luts, [v / s for v in y], name), name
        if space == b"GRAY":
            if b"kTRC" not in tags:
                raise ValueError("gray profile without kTRC")
            return IccProfile("gray", [_read_curve(data, tags[b"kTRC"][0])], None, name), name
        return None, f"{space.decode('latin-1').strip()} profile not supported"
    except Exception as e:
        return None, str(e)


_icc_cache = {}


def _parse_cached(data: bytes):
    key = hash(data)
    if key not in _icc_cache:
        if len(_icc_cache) > 16:
            _icc_cache.clear()
        _icc_cache[key] = parse_icc(data)
    return _icc_cache[key]


def icc_applicable(icc, channels: int, photo: str = "") -> bool:
    if icc is None or (photo or "").upper() in (
            "SEPARATED", "YCBCR", "CIELAB", "ICCLAB", "ITULAB", "PALETTE"):
        return False
    return channels >= 3 if icc.kind == "rgb" else channels in (1, 2)


def _icc_for_array(data, icc_mode, channels, photo, notes):
    """Resolve the profile to use for one frame and record a note."""
    if icc_mode != ICC_USE:
        return None
    if not data:
        notes.add("no embedded ICC profile: plain luma used")
        return None
    prof, msg = _parse_cached(bytes(data))
    if prof is None:
        notes.add(f"ICC not applied ({msg})")
        return None
    if not icc_applicable(prof, channels, photo):
        notes.add(f"ICC '{msg}' does not fit this image's channels: ignored")
        return None
    notes.add(f"ICC applied: {msg}")
    return prof


# --------------------------------------------------------------------------
# Pixel Level Transformations
# --------------------------------------------------------------------------

def frame_to_gray8(im: Image.Image, alpha_mode=ALPHA_WHITE, custom_color=(255, 255, 255),
                   hi_mode=HI_SCALE, icc=None) -> Image.Image:
    """Convert one frame to a true 8-bit grayscale ('L') image."""
    mode = im.mode
    bg = _background_rgb(alpha_mode, custom_color)

    # Optional ICC route for 8-bit L / LA / RGB / RGBA
    if icc is not None and mode in ("L", "LA", "RGB", "RGBA"):
        arr = np.array(im)
        if arr.ndim == 2:
            arr = arr[..., None]
        photo = "RGB" if arr.shape[2] >= 3 else ""
        if icc_applicable(icc, arr.shape[2], photo):
            extra = (2,) if mode in ("LA", "RGBA") else ()
            return array_to_gray8(arr, photo, extra, alpha_mode, custom_color, hi_mode, icc)

    # Plain 8-bit grayscale: keep pixels exactly as they are.
    if mode == "L":
        return im.copy()

    if mode == "1":
        return im.convert("L")

    # Grayscale + alpha
    if mode in ("LA", "La"):
        if mode == "La":
            im = im.convert("LA")
        g, a = im.split()
        if alpha_mode == ALPHA_IGNORE:
            return g
        background = Image.new("L", g.size, _luma(bg))
        return Image.composite(g, background, a)

    # Palette: expand to RGBA if it carries transparency
    if mode in ("P", "PA"):
        has_alpha = mode == "PA" or "transparency" in im.info
        im = im.convert("RGBA" if has_alpha else "RGB")
        mode = im.mode

    if mode in ("RGBA", "RGBa"):
        rgba = im.convert("RGBA")
        if alpha_mode == ALPHA_IGNORE:
            return rgba.convert("RGB").convert("L")
        background = Image.new("RGBA", rgba.size, bg + (255,))
        flat = Image.alpha_composite(background, rgba)
        return flat.convert("RGB").convert("L")

    if mode in ("RGB", "RGBX", "CMYK", "YCbCr", "LAB", "HSV"):
        return im.convert("RGB").convert("L")

    # 16-bit grayscale variants
    if mode.startswith("I;16"):
        arr = np.array(im).astype(np.uint16)
        return _array_to_u8(arr, hi_mode)

    # 32-bit integer / float
    if mode in ("I", "F"):
        return _array_to_u8(np.array(im), hi_mode)

    return im.convert("RGB").convert("L")


def array_to_gray8(arr: np.ndarray, photometric: str = "", extrasamples=(),
                   alpha_mode=ALPHA_WHITE, custom_color=(255, 255, 255),
                   hi_mode=HI_SCALE, icc=None) -> Image.Image:
    """Convert an (H, W, C) numpy array (any bit depth) to an 8-bit 'L' image."""
    if arr.ndim != 3:
        raise ValueError(f"unsupported array shape {arr.shape}")
    h, w, c = arr.shape
    kind = arr.dtype.kind
    photo = (photometric or "").upper()

    if kind == "b":
        raise ValueError("1-bit TIFF: handled by Pillow")
    if kind == "i":
        if c == 1:
            return _array_to_u8(arr[..., 0], hi_mode)
        raise ValueError("signed multi-channel TIFF not supported")
    if kind == "u":
        maxv = float(np.iinfo(arr.dtype).max)
        bits = arr.dtype.itemsize * 8
    elif kind == "f":
        maxv, bits = 1.0, 0
    else:
        raise ValueError(f"unsupported dtype {arr.dtype}")

    use_icc = icc is not None and icc_applicable(icc, c, photo)

    if c == 1 and arr.dtype == np.uint8 and photo != "MINISWHITE" and not use_icc:
        return Image.fromarray(np.ascontiguousarray(arr[..., 0]), "L")

    n = arr.astype(np.float64) / maxv
    if kind == "f":
        n = np.nan_to_num(n, nan=0.0, posinf=1.0, neginf=0.0)

    alpha = None
    premult = len(extrasamples) > 0 and int(extrasamples[0]) == 1  # ASSOCALPHA
    if photo == "SEPARATED":
        if c < 4:
            raise ValueError("CMYK TIFF with fewer than 4 channels")
        cc, mm, yy, kk = (n[..., i] for i in range(4))
        rgb = np.stack([(1 - cc) * (1 - kk), (1 - mm) * (1 - kk), (1 - yy) * (1 - kk)], axis=-1)
        g = rgb @ np.array([0.299, 0.587, 0.114])
        if c >= 5 and len(extrasamples) > 0:
            alpha = n[..., 4]
    elif photo in ("YCBCR",):
        raise ValueError("YCbCr TIFF: handled by Pillow")
    elif photo in ("CIELAB", "ICCLAB", "ITULAB"):
        g = n[..., 0]
    elif c == 1:
        g = n[..., 0]
    elif c == 2:
        g, alpha = n[..., 0], n[..., 1]
    else:  # RGB, RGBA, RGB + extra channels
        rgb = n[..., :3]
        if c >= 4 and (c == 4 or len(extrasamples) > 0):
            alpha = n[..., 3]
        if use_icc:
            if premult and alpha is not None:
                al = alpha[..., None]
                rgb = np.where(al > 0, rgb / np.maximum(al, 1e-12), 0.0)
                premult = False
            g = icc.to_gray(rgb)
        else:
            g = rgb @ np.array([0.299, 0.587, 0.114])
    if photo == "MINISWHITE" and c <= 2:
        g = 1.0 - g
    if use_icc and icc.kind == "gray":
        g = icc.to_gray(g)

    if alpha is not None and alpha_mode != ALPHA_IGNORE:
        a = np.clip(alpha, 0.0, 1.0)
        bgn = _luma(_background_rgb(alpha_mode, custom_color)) / 255.0
        g = (g + bgn * (1.0 - a)) if premult else (g * a + bgn * (1.0 - a))

    # reduce to 8 bit
    if bits == 8:
        out = np.rint(g * 255.0)
    elif hi_mode == HI_SCALE:
        lo, hi = float(g.min()), float(g.max())
        if hi > lo:
            out = np.rint((g - lo) * (255.0 / (hi - lo)))
        else:
            out = np.rint(np.clip(g, 0.0, 1.0) * 255.0)
    else:  # truncate
        if bits == 0:
            out = np.floor(np.clip(g, 0.0, 1.0) * 255.0)
        else:
            v = np.rint(np.clip(g, 0.0, 1.0) * maxv)
            out = np.floor(v / (2.0 ** (bits - 8)))
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "L")


def _main_pages(tf):
    pages = list(tf.pages)
    return [p for p in pages if not getattr(p, "is_reduced", False)] or pages


def _page_to_array(page):
    """Return (arr[H,W,C], photometric name, extrasamples, icc bytes or None)."""
    from tifffile import PLANARCONFIG
    photo = page.photometric.name if page.photometric is not None else ""
    if photo == "PALETTE":
        raise ValueError("palette TIFF: handled by Pillow")
    arr = page.asarray()
    if arr.ndim == 2:
        arr = arr[..., None]
    elif arr.ndim == 3 and page.planarconfig == PLANARCONFIG.SEPARATE:
        arr = np.moveaxis(arr, 0, -1)
    extra = tuple(page.extrasamples or ())
    icc = None
    tag = page.tags.get(34675)
    if tag is not None:
        v = tag.value
        icc = bytes(v) if isinstance(v, (bytes, bytearray)) else np.asarray(v).tobytes()
    return arr, photo, extra, icc


def iter_tiff_gray_frames(path: str, alpha_mode, custom_color, hi_mode,
                          icc_mode=ICC_IGNORE, notes=None):
    """Yield an 8-bit 'L' image per page, reading with tifffile."""
    notes = notes if notes is not None else set()
    with tifffile.TiffFile(path) as tf:
        for page in _main_pages(tf):
            arr, photo, extra, icc_bytes = _page_to_array(page)
            prof = _icc_for_array(icc_bytes, icc_mode, arr.shape[2], photo, notes)
            yield array_to_gray8(arr, photo, extra, alpha_mode, custom_color, hi_mode, prof)


def _pil_icc(im, icc_mode, notes):
    """Resolve the profile of a Pillow image (8-bit L/LA/RGB/RGBA only)."""
    if icc_mode != ICC_USE:
        return None
    ch = {"L": 1, "LA": 2, "RGB": 3, "RGBA": 4}.get(im.mode)
    if ch is None:
        notes.add(f"ICC not applied (mode {im.mode})")
        return None
    return _icc_for_array(im.info.get("icc_profile"), icc_mode, ch, "RGB" if ch >= 3 else "", notes)


def _load_gray_frames(src, alpha_mode, custom_color, hi_mode, icc_mode=ICC_IGNORE):
    """Return (frames, n_frames_in, reader_name, notes)."""
    errors = []
    notes = set()
    if tifffile is not None and _is_tiff_name(src):
        try:
            frames = list(iter_tiff_gray_frames(src, alpha_mode, custom_color, hi_mode,
                                                icc_mode, notes))
            if frames:
                return frames, len(frames), "tifffile", sorted(notes)
        except Exception as e:
            errors.append(f"tifffile: {e}")
    notes = set()
    try:
        with Image.open(src) as im:
            n_in = getattr(im, "n_frames", 1)
            frames = []
            for f in ImageSequence.Iterator(im):
                prof = _pil_icc(f, icc_mode, notes)
                frames.append(frame_to_gray8(f, alpha_mode, custom_color, hi_mode, prof))
        return frames, n_in, "Pillow", sorted(notes)
    except Exception as e:
        errors.append(f"Pillow: {e}")
    if _is_tiff_name(src) and tifffile is None:
        errors.append("tifffile not installed (pip install tifffile imagecodecs)")
    raise RuntimeError("; ".join(errors))


# --------------------------------------------------------------------------
# Preview (Original vs. 8-Bit Result)
# --------------------------------------------------------------------------

def _arr_to_display_rgba(arr, photo="", extra=()):
    """Approximate 8-bit RGBA rendering of the stored values."""
    kind = arr.dtype.kind
    if kind == "u":
        bits = arr.dtype.itemsize * 8
        d = (arr >> (bits - 8)) if bits > 8 else arr
        d = d.astype(np.uint8)
    elif kind == "f":
        d = np.rint(np.clip(np.nan_to_num(arr), 0, 1) * 255).astype(np.uint8)
    elif kind == "i":
        a = arr.astype(np.float64)
        lo, hi = a.min(), a.max()
        d = np.rint((a - lo) * (255.0 / (hi - lo if hi > lo else 1.0))).astype(np.uint8)
    else:
        raise ValueError(f"cannot display dtype {arr.dtype}")
    c = d.shape[2]
    photo = (photo or "").upper()
    alpha = None
    if photo == "SEPARATED" and c >= 4:
        f = d.astype(np.float64) / 255.0
        rgb = np.stack([(1 - f[..., i]) * (1 - f[..., 3]) for i in range(3)], -1) * 255.0
        rgb = np.rint(rgb).astype(np.uint8)
    elif c == 1:
        rgb = np.repeat(d, 3, axis=2)
    elif c == 2:
        rgb, alpha = np.repeat(d[..., :1], 3, axis=2), d[..., 1]
    else:
        rgb = d[..., :3]
        if c >= 4 and (c == 4 or len(extra) > 0):
            alpha = d[..., 3]
    if photo == "MINISWHITE" and c <= 2:
        rgb = 255 - rgb
    if alpha is not None and len(extra) > 0 and int(extra[0]) == 1:  # premultiplied
        al = np.maximum(alpha[..., None].astype(np.float64), 1.0)
        rgb = np.clip(rgb.astype(np.float64) * 255.0 / al, 0, 255).astype(np.uint8)
    out = np.dstack([rgb, alpha if alpha is not None else np.full(rgb.shape[:2], 255, np.uint8)])
    return Image.fromarray(np.ascontiguousarray(out), "RGBA")


def _pil_display_rgba(im):
    if im.mode.startswith("I;16") or im.mode in ("I", "F"):
        arr = np.array(im)
        return _arr_to_display_rgba(arr[..., None], "", ())
    return im.convert("RGBA")


def build_preview(path, frame_index=0, alpha_mode=ALPHA_WHITE, custom_color=(255, 255, 255),
                  hi_mode=HI_SCALE, icc_mode=ICC_IGNORE, max_side=1600) -> dict:
    """Render one frame: returns dict(before=RGBA, after=L, n_frames, info, notes)."""
    notes = set()
    reader = None
    if tifffile is not None and _is_tiff_name(path):
        try:
            with tifffile.TiffFile(path) as tf:
                pages = _main_pages(tf)
                n = len(pages)
                idx = min(max(int(frame_index), 0), n - 1)
                arr, photo, extra, icc_bytes = _page_to_array(pages[idx])
                prof = _icc_for_array(icc_bytes, icc_mode, arr.shape[2], photo, notes)
                after = array_to_gray8(arr, photo, extra, alpha_mode, custom_color, hi_mode, prof)
                before = _arr_to_display_rgba(arr, photo, extra)
                label = f"{arr.shape[2]}ch {arr.dtype}"
                reader = "tifffile"
        except Exception:
            notes = set()
            reader = None
    if reader is None:
        with Image.open(path) as im:
            n = getattr(im, "n_frames", 1)
            idx = min(max(int(frame_index), 0), n - 1)
            im.seek(idx)
            prof = _pil_icc(im, icc_mode, notes)
            after = frame_to_gray8(im, alpha_mode, custom_color, hi_mode, prof)
            before = _pil_display_rgba(im)
            label = im.mode
            reader = "Pillow"
    w, h = after.size
    before.thumbnail((max_side, max_side), Image.BILINEAR)
    after_small = after.copy()
    after_small.thumbnail((max_side, max_side), Image.BILINEAR)
    info = (f"{os.path.basename(path)}  |  frame {idx + 1}/{n}  |  {w} x {h}  |  "
            f"source {label}  |  read via {reader}")
    return {"before": before, "after": after_small, "n_frames": n, "frame": idx,
            "info": info, "notes": sorted(notes), "size": (w, h)}


# --------------------------------------------------------------------------
# File Conversion & Output Path Helpers
# --------------------------------------------------------------------------

def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    n = 1
    while True:
        cand = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not cand.exists():
            return cand
        n += 1


def build_output_path(src: str, out_dir: str, suffix: str, out_fmt: str, overwrite: bool) -> Path:
    srcp = Path(src)
    ext = ".bmp" if out_fmt == "BMP" else ".tif"
    folder = Path(out_dir) if out_dir else srcp.parent
    dest = folder / f"{srcp.stem}{suffix}{ext}"
    same_as_source = dest.resolve() == srcp.resolve()
    if same_as_source or (dest.exists() and not overwrite):
        dest = unique_path(dest)
    return dest


def convert_file(src: str, out_dir: str = "", suffix: str = "_8bit", out_fmt: str = "TIFF",
                 alpha_mode=ALPHA_WHITE, custom_color=(255, 255, 255),
                 hi_mode=HI_SCALE, overwrite: bool = False, icc_mode=ICC_IGNORE) -> dict:
    """Convert one file on disk. Returns dict(dest, frames_in, frames_out, note, reader)."""
    dest = build_output_path(src, out_dir, suffix, out_fmt, overwrite)
    dest.parent.mkdir(parents=True, exist_ok=True)

    frames, n_in, reader, icc_notes = _load_gray_frames(src, alpha_mode, custom_color,
                                                       hi_mode, icc_mode)

    note = ""
    if out_fmt == "BMP":
        frames[0].save(dest, format="BMP")
        n_out = 1
        if n_in > 1:
            note = f"multi-page ({n_in}) -> BMP: first frame only"
    else:
        if len(frames) > 1:
            frames[0].save(dest, format="TIFF", compression="tiff_lzw",
                           save_all=True, append_images=frames[1:])
        else:
            frames[0].save(dest, format="TIFF", compression="tiff_lzw")
        n_out = len(frames)
    note = "; ".join([x for x in [note] + icc_notes if x])
    return {"dest": str(dest), "frames_in": n_in, "frames_out": n_out, "note": note,
            "reader": reader}


def collect_images(folder: str, recursive: bool = False):
    root = Path(folder)
    it = root.rglob("*") if recursive else root.glob("*")
    return sorted(str(p) for p in it if p.is_file() and p.suffix.lower() in SUPPORTED_EXT)


# --------------------------------------------------------------------------
# Web / Pyodide WASM Bridge Helpers
# --------------------------------------------------------------------------

def _image_to_data_url(im: Image.Image, fmt="PNG") -> str:
    buf = io.BytesIO()
    im.save(buf, format=fmt)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/{fmt.lower()};base64,{b64}"


def web_describe_path(path: str) -> dict:
    """Extract metadata for UI table from path (used by Pyodide)."""
    info = describe_image(path)
    return {
        "name": info.get("name", os.path.basename(path)),
        "format": info.get("format", "?"),
        "mode": info.get("mode", "?"),
        "depth": info.get("depth", "?"),
        "size": info.get("size", "?"),
        "frames": info.get("frames", 1),
    }


def web_preview_path(path: str, frame_index=0, alpha_mode=ALPHA_WHITE,
                     custom_color="#ffffff", hi_mode=HI_SCALE,
                     icc_mode=ICC_IGNORE, max_side=1200) -> dict:
    """Render preview images to data URLs for direct browser display."""
    res = build_preview(path, frame_index=frame_index, alpha_mode=alpha_mode,
                        custom_color=custom_color, hi_mode=hi_mode,
                        icc_mode=icc_mode, max_side=max_side)
    return {
        "before_url": _image_to_data_url(res["before"], "PNG"),
        "after_url": _image_to_data_url(res["after"], "PNG"),
        "n_frames": res["n_frames"],
        "frame": res["frame"],
        "info": res["info"],
        "notes": res["notes"],
        "width": res["size"][0],
        "height": res["size"][1],
    }


def web_convert_path(path: str, out_fmt="TIFF", alpha_mode=ALPHA_WHITE,
                     custom_color="#ffffff", hi_mode=HI_SCALE,
                     icc_mode=ICC_IGNORE, suffix="_8bit") -> dict:
    """Convert file and return output bytes and metadata (used by Pyodide)."""
    frames, n_in, reader, icc_notes = _load_gray_frames(path, alpha_mode, custom_color,
                                                       hi_mode, icc_mode)
    buf = io.BytesIO()
    ext = ".bmp" if out_fmt == "BMP" else ".tif"
    stem = Path(path).stem
    out_filename = f"{stem}{suffix}{ext}"
    note = ""

    if out_fmt == "BMP":
        frames[0].save(buf, format="BMP")
        n_out = 1
        if n_in > 1:
            note = f"multi-page ({n_in}) -> BMP: first frame only"
    else:
        if len(frames) > 1:
            frames[0].save(buf, format="TIFF", compression="tiff_lzw",
                           save_all=True, append_images=frames[1:])
        else:
            frames[0].save(buf, format="TIFF", compression="tiff_lzw")
        n_out = len(frames)

    note = "; ".join([x for x in [note] + icc_notes if x])
    out_bytes = buf.getvalue()
    return {
        "filename": out_filename,
        "bytes": out_bytes,
        "frames_in": n_in,
        "frames_out": n_out,
        "note": note,
        "reader": reader,
        "size_bytes": len(out_bytes),
    }
