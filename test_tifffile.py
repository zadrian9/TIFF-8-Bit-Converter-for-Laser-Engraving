"""V2 tests: Affinity-style TIFFs (16-bit / float, RGB / RGBA / planar / CMYK) via tifffile."""
import os, tempfile
import numpy as np
from PIL import Image
import tifffile
import converter_core as c

tmp = tempfile.mkdtemp()
fails = 0

def check(name, cond, extra=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + (f"   {extra}" if not cond else ""))
    fails += 0 if cond else 1

def W(name, arr, **kw):
    p = os.path.join(tmp, name); tifffile.imwrite(p, arr, **kw); return p

def conv(p, **kw):
    r = c.convert_file(p, out_dir=tmp, suffix="_o", overwrite=True, **kw)
    with Image.open(r["dest"]) as im:
        im.load(); return np.array(im), r

H = Wd = 4
# --- 16-bit RGB, pure red -> luma 0.299
red = np.zeros((H, Wd, 3), np.uint16); red[..., 0] = 65535
o, r = conv(W("rgb16.tif", red, photometric="rgb"), hi_mode=c.HI_TRUNCATE)
check("RGB16 read via tifffile", r["reader"] == "tifffile")
check("RGB16 red truncate", o.dtype == np.uint8 and o[0, 0] == int(round(0.299 * 65535) // 256), str(o[0, 0]))
o, _ = conv(W("rgb16b.tif", red, photometric="rgb"), hi_mode=c.HI_SCALE)
check("RGB16 constant scale ok", abs(int(o[0, 0]) - round(0.299 * 255)) <= 1, str(o[0, 0]))

# --- 16-bit RGB gray ramp, scale and truncate
ramp = np.tile(np.array([0, 256, 512, 65535], np.uint16), (H, 1))
rgb_ramp = np.stack([ramp] * 3, axis=-1)
o, _ = conv(W("ramp.tif", rgb_ramp, photometric="rgb"), hi_mode=c.HI_TRUNCATE)
check("RGB16 ramp truncate == >>8", o[0].tolist() == [0, 1, 2, 255], str(o[0]))
o, _ = conv(W("ramp2.tif", rgb_ramp, photometric="rgb"), hi_mode=c.HI_SCALE)
check("RGB16 ramp scale", o[0, 0] == 0 and o[0, 3] == 255 and o[0, 1] == round(256 * 255 / 65535), str(o[0]))

# --- 16-bit RGBA with alpha (Affinity default export style)
rgba = np.zeros((H, Wd, 4), np.uint16)
rgba[..., :3] = 20000; rgba[..., 3] = 32768
p = W("rgba16.tif", rgba, photometric="rgb", extrasamples=["unassalpha"])
o, r = conv(p, hi_mode=c.HI_TRUNCATE)
a = 32768 / 65535
exp = int(np.floor(np.rint((20000 / 65535 * a + 1 * (1 - a)) * 65535) / 256))
check("RGBA16 composite white", r["reader"] == "tifffile" and abs(int(o[0, 0]) - exp) <= 1, f"{o[0,0]} vs {exp}")
o, _ = conv(p, hi_mode=c.HI_TRUNCATE, alpha_mode=c.ALPHA_BLACK)
expb = int(np.floor(np.rint((20000 / 65535 * a) * 65535) / 256))
check("RGBA16 composite black", abs(int(o[0, 0]) - expb) <= 1, f"{o[0,0]} vs {expb}")
o, _ = conv(p, hi_mode=c.HI_TRUNCATE, alpha_mode=c.ALPHA_IGNORE)
check("RGBA16 ignore alpha", int(o[0, 0]) == 20000 // 256)
rgba0 = np.zeros((H, Wd, 4), np.uint16)
o, _ = conv(W("rgba16t.tif", rgba0, photometric="rgb", extrasamples=["unassalpha"]), hi_mode=c.HI_TRUNCATE)
check("RGBA16 transparent -> white", int(o[0, 0]) == 255)
o, _ = conv(W("rgba16t2.tif", rgba0, photometric="rgb", extrasamples=["unassalpha"]), hi_mode=c.HI_SCALE)
check("RGBA16 transparent -> white (scale)", int(o[0, 0]) == 255)

# --- 4-channel RGB 16-bit with no declared extrasamples still treated as RGBA
# --- gray 16 + alpha 16
la16 = np.zeros((H, Wd, 2), np.uint16); la16[..., 0] = 0; la16[..., 1] = 65535
o, _ = conv(W("la16.tif", la16, photometric="minisblack", extrasamples=["unassalpha"]), hi_mode=c.HI_TRUNCATE)
check("LA16 opaque black stays 0", int(o[0, 0]) == 0)

# --- float32 RGB 0..1
f = np.full((H, Wd, 3), 0.5, np.float32)
o, r = conv(W("f32.tif", f, photometric="rgb"), hi_mode=c.HI_TRUNCATE)
check("float32 RGB truncate", r["reader"] == "tifffile" and int(o[0, 0]) == 127, str(o[0, 0]))
fr = np.tile(np.array([0.0, 0.25, 0.5, 1.0], np.float32), (H, 1))
o, _ = conv(W("f32r.tif", np.stack([fr] * 3, -1), photometric="rgb"), hi_mode=c.HI_SCALE)
check("float32 RGB scale", o[0].tolist() == [0, 64, 128, 255], str(o[0]))

# --- planar (separate) RGB 16
planar = np.moveaxis(rgb_ramp, -1, 0).copy()   # (3,H,W)
o, _ = conv(W("planar.tif", planar, photometric="rgb", planarconfig="separate"), hi_mode=c.HI_TRUNCATE)
check("planar RGB16", o[0].tolist() == [0, 1, 2, 255], str(o[0]))

# --- compression that needs no imagecodecs (deflate) and with predictor
try:
    o, r = conv(W("deflate.tif", rgb_ramp, photometric="rgb", compression="deflate"), hi_mode=c.HI_TRUNCATE)
    check("deflate RGB16", r["reader"] == "tifffile" and o[0].tolist() == [0, 1, 2, 255], str(o[0]))
except Exception as e:
    check("deflate RGB16", False, str(e))

# --- 8-bit via tifffile: CMYK, premultiplied alpha, plain L exact
cmyk = np.zeros((H, Wd, 4), np.uint8)  # no ink = white
o, _ = conv(W("cmyk.tif", cmyk, photometric="separated"))
check("CMYK8 white", int(o[0, 0]) == 255, str(o[0, 0]))
pm = np.zeros((H, Wd, 4), np.uint8); pm[..., :3] = 50; pm[..., 3] = 128
o, _ = conv(W("pm.tif", pm, photometric="rgb", extrasamples=["assocalpha"]))
check("premultiplied alpha", abs(int(o[0, 0]) - 177) <= 1, str(o[0, 0]))
g8 = np.arange(256, dtype=np.uint8).reshape(16, 16)
o, r = conv(W("g8.tif", g8, photometric="minisblack"))
check("L8 exact via tifffile", r["reader"] == "tifffile" and np.array_equal(o, g8))
o, _ = conv(W("g8w.tif", g8, photometric="miniswhite"))
check("MINISWHITE inverted", np.array_equal(o, 255 - g8))

# --- multipage 16-bit gray
pages = np.stack([np.full((8, 8), 256 * 10, np.uint16), np.full((8, 8), 256 * 200, np.uint16)])
p = W("mp16.tif", pages, photometric="minisblack")
r = c.convert_file(p, out_dir=tmp, suffix="_mp", overwrite=True, hi_mode=c.HI_TRUNCATE)
with Image.open(r["dest"]) as im:
    check("multipage 16-bit kept", im.n_frames == 2 and r["frames_out"] == 2)
    im.seek(1); check("multipage frame 2", int(np.array(im)[0, 0]) == 200)

# --- describe_image shows true depth
d = c.describe_image(os.path.join(tmp, "rgba16.tif"))
check("describe RGBA16", d["mode"] == "RGBA;16" and "16-bit" in d["depth"] and d["size"] == "4 x 4", str(d))
d = c.describe_image(os.path.join(tmp, "f32.tif"))
check("describe float", "float" in d["depth"], str(d))

# --- fallback to Pillow: palette TIFF, BMP
pim = Image.new("P", (4, 4), 0); pim.putpalette([0, 0, 0, 255, 255, 255] + [0] * 762)
pp = os.path.join(tmp, "pal.tif"); pim.save(pp)
o, r = conv(pp)
check("palette TIFF falls back to Pillow", r["reader"] == "Pillow" and o.mode if False else r["reader"] == "Pillow")
bp = os.path.join(tmp, "x.bmp"); Image.fromarray(g8, "L").save(bp)
o, r = conv(bp)
check("BMP uses Pillow", r["reader"] == "Pillow" and np.array_equal(o, g8))

# --- broken file gives a readable error, not a crash
bad = os.path.join(tmp, "bad.tif"); open(bad, "wb").write(b"not a tiff at all")
try:
    c.convert_file(bad, out_dir=tmp); check("bad file raises", False)
except Exception as e:
    check("bad file raises readable error", "Pillow" in str(e) or "tifffile" in str(e), str(e))

print("\nFAILURES:", fails)
raise SystemExit(1 if fails else 0)
