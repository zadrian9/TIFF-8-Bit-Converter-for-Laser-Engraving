"""V2.1 tests: ICC handling and the preview function."""
import os, struct, tempfile
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

def srgb_enc(x):
    x = min(max(x, 0.0), 1.0)
    return 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055

# ---- minimal ICC profile builder -------------------------------------------------
def s15(x): return struct.pack(">i", int(round(x * 65536)))
def xyz(X, Y, Z): return b"XYZ \0\0\0\0" + s15(X) + s15(Y) + s15(Z)
def curv_gamma(g): return b"curv\0\0\0\0" + struct.pack(">I", 1) + struct.pack(">H", int(round(g * 256))) + b"\0\0"
def curv_ident(): return b"curv\0\0\0\0" + struct.pack(">I", 0)
def para_srgb():
    p = [2.4, 1 / 1.055, 0.055 / 1.055, 1 / 12.92, 0.04045]
    return b"para\0\0\0\0" + struct.pack(">HH", 3, 0) + b"".join(s15(v) for v in p)
def desc(text):
    t = text.encode() + b"\0"
    return b"desc\0\0\0\0" + struct.pack(">I", len(t)) + t + b"\0" * 78

def build(space, tags):
    n = len(tags); off = 128 + 4 + 12 * n; body = b""; table = b""
    for sig, data in tags.items():
        data += b"\0" * (-len(data) % 4)
        table += sig + struct.pack(">II", off + len(body), len(data)); body += data
    h = bytearray(128); h[0:4] = struct.pack(">I", off + len(body)); h[8] = 2
    h[12:16] = b"mntr"; h[16:20] = space; h[20:24] = b"XYZ "; h[36:40] = b"acsp"
    return bytes(h) + struct.pack(">I", n) + table + body

def rgb_profile(curve, name="test"):
    return build(b"RGB ", {b"desc": desc(name), b"rXYZ": xyz(0.4360, 0.2225, 0.0139),
                           b"gXYZ": xyz(0.3851, 0.7169, 0.0971), b"bXYZ": xyz(0.1431, 0.0606, 0.7141),
                           b"rTRC": curve, b"gTRC": curve, b"bTRC": curve})

def gray_profile(curve, name="grayprof"):
    return build(b"GRAY", {b"desc": desc(name), b"kTRC": curve})

def W(name, arr, **kw):
    p = os.path.join(tmp, name); tifffile.imwrite(p, arr, **kw); return p

def conv(p, **kw):
    r = c.convert_file(p, out_dir=tmp, suffix="_o", overwrite=True, **kw)
    with Image.open(r["dest"]) as im:
        im.load(); return np.array(im), r

# ---- parsing --------------------------------------------------------------------
prof, name = c.parse_icc(rgb_profile(para_srgb(), "My sRGB"))
check("parse para RGB profile + name", prof is not None and name == "My sRGB" and prof.kind == "rgb", str(name))
check("Y weights sum to 1", abs(sum(prof.ycoef) - 1) < 1e-9)
prof_g, _ = c.parse_icc(gray_profile(curv_gamma(2.2)))
check("parse gray profile", prof_g is not None and prof_g.kind == "gray")
p_lut, why = c.parse_icc(build(b"RGB ", {b"desc": desc("lut")}))
check("LUT-style RGB rejected with reason", p_lut is None and "LUT" in why, why)
p_bad, why = c.parse_icc(b"garbage" * 40)
check("garbage rejected", p_bad is None)
p_cmyk, why = c.parse_icc(build(b"CMYK", {b"desc": desc("c")}))
check("CMYK profile not supported (message)", p_cmyk is None and "CMYK" in why, why)

# ---- sRGB-like profile: neutral gray is identity; colours become true luminance --
srgb_bytes = rgb_profile(para_srgb())
neutral = np.tile(np.arange(256, dtype=np.uint8)[:, None, None], (1, 4, 3))
p = W("neutral.tif", neutral, photometric="rgb", iccprofile=srgb_bytes)
o, r = conv(p, icc_mode=c.ICC_USE)
check("sRGB neutral ramp unchanged", np.array_equal(o[:, 0], np.arange(256)), str(np.abs(o[:, 0].astype(int) - np.arange(256)).max()))
check("note says ICC applied", any("ICC applied: test" in x for x in r["note"].split("; ")), r["note"])
red = np.zeros((4, 4, 3), np.uint8); red[..., 0] = 255
p = W("red_icc.tif", red, photometric="rgb", iccprofile=srgb_bytes)
o_use, _ = conv(p, icc_mode=c.ICC_USE)
o_ign, _ = conv(p, icc_mode=c.ICC_IGNORE)
exp = int(round(255 * srgb_enc(0.2225)))
check("red with ICC = colorimetric luminance", abs(int(o_use[0, 0]) - exp) <= 1, f"{o_use[0,0]} vs {exp}")
check("red ignoring ICC = plain luma (76)", abs(int(o_ign[0, 0]) - 76) <= 1, str(o_ign[0, 0]))
check("default is ignore", np.array_equal(conv(p)[0], o_ign))

# ---- gamma 2.2 and linear profiles ------------------------------------------------
g128 = np.full((4, 4, 3), 128, np.uint8)
o, _ = conv(W("g22.tif", g128, photometric="rgb", iccprofile=rgb_profile(curv_gamma(2.2))), icc_mode=c.ICC_USE)
exp = int(round(255 * srgb_enc((128 / 255) ** 2.2)))
check("gamma 2.2 profile", abs(int(o[0, 0]) - exp) <= 1, f"{o[0,0]} vs {exp}")
o, _ = conv(W("lin.tif", g128, photometric="rgb", iccprofile=rgb_profile(curv_ident())), icc_mode=c.ICC_USE)
exp = int(round(255 * srgb_enc(128 / 255)))
check("linear profile brightens", abs(int(o[0, 0]) - exp) <= 1, f"{o[0,0]} vs {exp}")

# ---- 16-bit + float with linear profile ------------------------------------------
v16 = np.full((4, 4, 3), 32768, np.uint16)
o, _ = conv(W("lin16.tif", v16, photometric="rgb", iccprofile=rgb_profile(curv_ident())),
            icc_mode=c.ICC_USE, hi_mode=c.HI_TRUNCATE)
exp = int(np.rint(srgb_enc(32768 / 65535) * 65535) // 256)
check("16-bit linear profile truncate", abs(int(o[0, 0]) - exp) <= 1, f"{o[0,0]} vs {exp}")
f32 = np.full((4, 4, 3), 0.5, np.float32)
o, _ = conv(W("f32icc.tif", f32, photometric="rgb", iccprofile=rgb_profile(curv_ident())),
            icc_mode=c.ICC_USE, hi_mode=c.HI_TRUNCATE)
exp = int(np.floor(srgb_enc(0.5) * 255))
check("float32 linear profile truncate", abs(int(o[0, 0]) - exp) <= 1, f"{o[0,0]} vs {exp}")

# ---- gray profile -----------------------------------------------------------------
g8 = np.tile(np.arange(0, 256, 5, dtype=np.uint8)[:, None], (1, 4))
o, _ = conv(W("gray_icc.tif", g8, photometric="minisblack", iccprofile=gray_profile(curv_gamma(2.2))),
            icc_mode=c.ICC_USE)
exp = np.array([int(round(255 * srgb_enc((v / 255) ** 2.2))) for v in g8[:, 0]])
check("gray 2.2 profile on L image", np.abs(o[:, 0].astype(int) - exp).max() <= 1, str(np.abs(o[:, 0].astype(int) - exp).max()))
o, _ = conv(W("gray_icc2.tif", g8, photometric="minisblack", iccprofile=gray_profile(curv_gamma(2.2))))
check("same L image untouched when ignoring ICC", np.array_equal(o, g8))

# ---- alpha + ICC ------------------------------------------------------------------
rgba = np.zeros((4, 4, 4), np.uint8); rgba[..., :3] = 100; rgba[..., 3] = 128
p = W("rgba_icc.tif", rgba, photometric="rgb", extrasamples=["unassalpha"], iccprofile=srgb_bytes)
a, b = conv(p, icc_mode=c.ICC_USE)[0], conv(p, icc_mode=c.ICC_IGNORE)[0]
check("RGBA + sRGB profile composites like plain neutral", abs(int(a[0, 0]) - int(b[0, 0])) <= 1, f"{a[0,0]} {b[0,0]}")
pm = np.zeros((4, 4, 4), np.uint8); pm[..., :3] = 50; pm[..., 3] = 128
p = W("pm_icc.tif", pm, photometric="rgb", extrasamples=["assocalpha"], iccprofile=srgb_bytes)
o, _ = conv(p, icc_mode=c.ICC_USE)
unp = 50 / 255 / (128 / 255)
exp = int(round(255 * (srgb_enc(0.0) + 0)))  # placeholder to keep flake quiet
lin_src = ((unp + 0.055) / 1.055) ** 2.4
# straight colour = unp (encoded); composite in encoded domain: unp*a + 1*(1-a)
exp = int(round(255 * (unp * (128 / 255) + (1 - 128 / 255))))
check("premultiplied + ICC un-premultiplied first", abs(int(o[0, 0]) - exp) <= 1, f"{o[0,0]} vs {exp}")

# ---- no profile / unsupported / mismatch --------------------------------------------
p = W("noprof.tif", red, photometric="rgb")
o, r = conv(p, icc_mode=c.ICC_USE)
check("no profile -> plain luma + note", abs(int(o[0, 0]) - 76) <= 1 and "no embedded ICC profile" in r["note"], r["note"])
lut_bytes = build(b"RGB ", {b"desc": desc("lutprof")})
o, r = conv(W("lutprof.tif", red, photometric="rgb", iccprofile=lut_bytes), icc_mode=c.ICC_USE)
check("unsupported profile -> no crash, note", abs(int(o[0, 0]) - 76) <= 1 and "ICC not applied" in r["note"], r["note"])
o, r = conv(W("mismatch.tif", g8, photometric="minisblack", iccprofile=srgb_bytes), icc_mode=c.ICC_USE)
check("RGB profile on gray image ignored", np.array_equal(o, g8) and "does not fit" in r["note"], r["note"])

# ---- Pillow path with ICC (direct) --------------------------------------------------
im = Image.new("RGB", (4, 4), (255, 0, 0))
o = np.array(c.frame_to_gray8(im, icc=prof))
check("Pillow RGB frame with ICC", abs(int(o[0, 0]) - int(round(255 * srgb_enc(0.2225)))) <= 1, str(o[0, 0]))
# icc embedded in a Pillow-readable BMP-less case: palette TIFF falls back to Pillow, note on mode
pim = Image.new("P", (4, 4), 0); pim.putpalette([0, 0, 0, 255, 255, 255] + [0] * 762)
pp = os.path.join(tmp, "pal.tif"); pim.save(pp)
o, r = conv(pp, icc_mode=c.ICC_USE)
check("palette TIFF + ICC mode: no crash", r["reader"] == "Pillow")

# Pillow-generated sRGB profile (if littlecms available)
try:
    from PIL import ImageCms
    pb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    pr, nm = c.parse_icc(pb)
    ok = pr is not None
    if ok:
        gray = np.array([[v, v, v] for v in range(256)], np.uint8).reshape(256, 1, 3)
        out = np.array(c.array_to_gray8(gray, "RGB", (), icc=pr)).reshape(-1)
        check("Pillow-built sRGB profile: neutral ramp identity", np.abs(out.astype(int) - np.arange(256)).max() <= 1,
              str(np.abs(out.astype(int) - np.arange(256)).max()))
    else:
        check("Pillow-built sRGB profile parses", False, nm)
except ImportError:
    print("SKIP ImageCms not available")

# ---- preview ------------------------------------------------------------------------
rgba16 = np.zeros((20, 30, 4), np.uint16); rgba16[..., :3] = 20000; rgba16[..., 3] = 32768
p = W("pv16.tif", rgba16, photometric="rgb", extrasamples=["unassalpha"])
pv = c.build_preview(p, 0, c.ALPHA_WHITE, (255, 255, 255), c.HI_TRUNCATE)
check("preview modes", pv["before"].mode == "RGBA" and pv["after"].mode == "L")
check("preview sizes + info", pv["after"].size == (30, 20) and pv["n_frames"] == 1 and "tifffile" in pv["info"], pv["info"])
check("preview before shows alpha", int(np.array(pv["before"])[0, 0, 3]) == int(round(32768 / 65535 * 255)) or abs(int(np.array(pv["before"])[0, 0, 3]) - 128) <= 1)
conv_arr, _ = conv(p, hi_mode=c.HI_TRUNCATE)
check("preview after == convert_file result", np.array_equal(np.array(pv["after"]), conv_arr))
pv2 = c.build_preview(p, 0, c.ALPHA_BLACK, hi_mode=c.HI_TRUNCATE)
check("preview responds to alpha option", int(np.array(pv2["after"])[0, 0]) < int(np.array(pv["after"])[0, 0]))

pages = np.stack([np.full((8, 8), 2560, np.uint16), np.full((8, 8), 51200, np.uint16)])
mp = W("pvmp.tif", pages, photometric="minisblack")
a0 = c.build_preview(mp, 0, hi_mode=c.HI_TRUNCATE); a1 = c.build_preview(mp, 1, hi_mode=c.HI_TRUNCATE)
check("preview multipage frames", a0["n_frames"] == 2 and int(np.array(a0["after"])[0, 0]) == 10 and int(np.array(a1["after"])[0, 0]) == 200)
a9 = c.build_preview(mp, 99, hi_mode=c.HI_TRUNCATE)
check("preview frame index clamped", a9["frame"] == 1)

big = np.tile((np.arange(3000) % 256).astype(np.uint8), (200, 1))
pb = c.build_preview(W("big.tif", big, photometric="minisblack"))
check("preview downscaled to 1600", max(pb["after"].size) == 1600 and pb["size"] == (3000, 200), str(pb["after"].size))

pv_icc = c.build_preview(W("pvicc.tif", red, photometric="rgb", iccprofile=srgb_bytes), 0, icc_mode=c.ICC_USE)
check("preview ICC note", any("ICC applied" in x for x in pv_icc["notes"]), str(pv_icc["notes"]))
pv_icc2 = c.build_preview(os.path.join(tmp, "red_icc.tif"), 0, icc_mode=c.ICC_IGNORE)
check("preview ICC changes result", int(np.array(pv_icc["after"])[0, 0]) != int(np.array(pv_icc2["after"])[0, 0]))

# Pillow fallbacks in preview
bp = os.path.join(tmp, "x.bmp"); Image.fromarray(g8, "L").save(bp)
pbm = c.build_preview(bp)
check("preview BMP via Pillow", "Pillow" in pbm["info"] and np.array_equal(np.array(pbm["after"]), g8))
ppv = c.build_preview(pp)
check("preview palette TIFF via Pillow", "Pillow" in ppv["info"])
pi = Image.fromarray(np.array([[0, 1000, 65535]], np.uint16))
ip = os.path.join(tmp, "i16.png"); pi.save(ip)
check("preview 16-bit PNG (Pillow I;16)", c.build_preview(ip)["before"].mode == "RGBA")

print("\nFAILURES:", fails)
raise SystemExit(1 if fails else 0)
