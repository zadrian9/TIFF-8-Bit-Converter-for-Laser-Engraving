import os
import tempfile
import numpy as np
from PIL import Image
import converter_core as c

tmp = tempfile.mkdtemp()
fails = 0

def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    fails += 0 if cond else 1

def save(im, name, **kw):
    p = os.path.join(tmp, name)
    im.save(p, **kw)
    return p

def load(p):
    with Image.open(p) as im:
        im.load()
        return im.copy()

# L: pixel-exact
g = np.arange(256, dtype=np.uint8).reshape(16, 16)
p = save(Image.fromarray(g, "L"), "l.tif")
r = c.convert_file(p, out_dir=tmp, suffix="_o")
o = load(r["dest"])
check("L preserved exactly", o.mode == "L" and np.array_equal(np.array(o), g))

# LA: white composite, half alpha
la = Image.new("LA", (4, 4), (100, 128))
p = save(la, "la.tif")
o = load(c.convert_file(p, out_dir=tmp, suffix="_w")["dest"])
exp = round((100 * 128 + 255 * 127) / 255)
check("LA onto white", o.mode == "L" and abs(int(np.array(o)[0, 0]) - exp) <= 1)
o = load(c.convert_file(p, out_dir=tmp, suffix="_b", alpha_mode=c.ALPHA_BLACK)["dest"])
check("LA onto black", abs(int(np.array(o)[0, 0]) - round(100 * 128 / 255)) <= 1)
o = load(c.convert_file(p, out_dir=tmp, suffix="_i", alpha_mode=c.ALPHA_IGNORE)["dest"])
check("LA ignore alpha", int(np.array(o)[0, 0]) == 100)
o = load(c.convert_file(p, out_dir=tmp, suffix="_c", alpha_mode=c.ALPHA_CUSTOM,
                        custom_color="#808080")["dest"])
check("LA custom bg", abs(int(np.array(o)[0, 0]) - round((100 * 128 + 128 * 127) / 255)) <= 1)

# Fully transparent LA -> background
la0 = Image.new("LA", (4, 4), (0, 0))
o = load(c.convert_file(save(la0, "la0.tif"), out_dir=tmp, suffix="_t")["dest"])
check("transparent -> white", int(np.array(o)[0, 0]) == 255)

# RGB / RGBA
rgb = Image.new("RGB", (4, 4), (255, 0, 0))
o = load(c.convert_file(save(rgb, "rgb.tif"), out_dir=tmp, suffix="_g")["dest"])
check("RGB->L", o.mode == "L" and int(np.array(o)[0, 0]) == rgb.convert("L").getpixel((0, 0)))
rgba = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
o = load(c.convert_file(save(rgba, "rgba.tif"), out_dir=tmp, suffix="_g")["dest"])
check("RGBA transparent -> white", int(np.array(o)[0, 0]) == 255)

# P with transparency
pim = Image.new("P", (4, 4), 0)
pim.putpalette([0, 0, 0] * 256)
pim.info["transparency"] = 0
o = c.frame_to_gray8(pim)
check("P transparent -> white", int(np.array(o)[0, 0]) == 255)

# 16-bit
a16 = np.array([[0, 256, 512, 65535]], dtype=np.uint16)
im16 = Image.fromarray(a16)
check("16-bit mode is I;16", im16.mode.startswith("I;16"))
o = np.array(c.frame_to_gray8(im16, hi_mode=c.HI_TRUNCATE))
check("16-bit truncate >>8", o.tolist() == [[0, 1, 2, 255]])
o = np.array(c.frame_to_gray8(im16, hi_mode=c.HI_SCALE))
check("16-bit scale", o[0, 0] == 0 and o[0, 3] == 255 and o[0, 1] == round(256 * 255 / 65535))

# 16-bit via saved TIFF round trip
p = save(im16, "g16.tif")
o = load(c.convert_file(p, out_dir=tmp, suffix="_t", hi_mode=c.HI_TRUNCATE)["dest"])
check("16-bit TIFF file", np.array(o).tolist() == [[0, 1, 2, 255]])

# I and F
o = np.array(c.frame_to_gray8(Image.fromarray(np.array([[0, 1000, 2000]], dtype=np.int32), "I")))
check("I scale", o.tolist() == [[0, 128, 255]])
o = np.array(c.frame_to_gray8(Image.fromarray(np.array([[0.0, 0.5, 1.0]], dtype=np.float32), "F")))
check("F scale", o.tolist() == [[0, 128, 255]])

# Multipage
f1 = Image.fromarray(np.full((8, 8), 10, np.uint8))
f2 = Image.fromarray(np.full((8, 8), 200, np.uint8))
p = os.path.join(tmp, "mp.tif")
f1.save(p, save_all=True, append_images=[f2])
r = c.convert_file(p, out_dir=tmp, suffix="_o")
with Image.open(r["dest"]) as im:
    check("multipage TIFF kept", im.n_frames == 2 and r["frames_out"] == 2)
    im.seek(1)
    check("frame 2 data", int(np.array(im)[0, 0]) == 200)

r = c.convert_file(p, out_dir=tmp, suffix="_o", out_fmt="BMP")
check("multipage -> BMP first frame", r["frames_out"] == 1 and "first frame" in r["note"]
      and r["dest"].endswith(".bmp"))
b = load(r["dest"])
check("BMP is 8-bit gray values", int(np.array(b.convert("L"))[0, 0]) == 10)

# BMP input
bp = save(Image.fromarray(g, "L"), "in.bmp")
o = load(c.convert_file(bp, out_dir=tmp, suffix="_o")["dest"])
check("BMP -> TIFF", o.mode == "L" and np.array_equal(np.array(o), g))

# Overwrite protection
r1 = c.convert_file(p, out_dir=tmp, suffix="_ow")
r2 = c.convert_file(p, out_dir=tmp, suffix="_ow")
r3 = c.convert_file(p, out_dir=tmp, suffix="_ow", overwrite=True)
check("no overwrite -> renamed", r1["dest"] != r2["dest"] and r2["dest"].endswith("_ow_1.tif"))
check("overwrite flag honored", r3["dest"] == r1["dest"])

# Empty suffix must not clobber the source
r = c.convert_file(p, out_dir="", suffix="")
check("never overwrites source", r["dest"] != p)

# Web helpers test (web_describe_path, web_preview_path, web_convert_path)
web_desc = c.web_describe_path(p)
check("web_describe returns valid dict", web_desc["format"] == "TIFF" and web_desc["frames"] == 2)

web_prev = c.web_preview_path(p)
check("web_preview returns data URLs", web_prev["before_url"].startswith("data:image/png;base64,") and web_prev["after_url"].startswith("data:image/png;base64,"))

web_conv = c.web_convert_path(p)
check("web_convert returns valid bytes", isinstance(web_conv["bytes"], bytes) and len(web_conv["bytes"]) > 0 and web_conv["frames_out"] == 2)

print("\nFAILURES:", fails)
raise SystemExit(1 if fails else 0)
