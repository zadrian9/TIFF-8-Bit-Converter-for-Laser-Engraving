"""
TIFF / BMP -> 8-bit Grayscale Converter (Desktop Tkinter GUI)
Imports core conversion engine from converter_core.py
"""

import os
import queue
import threading
from pathlib import Path

from PIL import Image

import converter_core as core
from converter_core import (
    VERSION, SUPPORTED_EXT,
    ALPHA_WHITE, ALPHA_BLACK, ALPHA_IGNORE, ALPHA_CUSTOM,
    ICC_IGNORE, ICC_USE,
    HI_SCALE, HI_TRUNCATE,
    describe_image, build_preview, convert_file, collect_images
)


def run_gui(test_hook=None):
    import tkinter as tk
    from tkinter import colorchooser, filedialog, messagebox, ttk
    from PIL import ImageTk

    root = tk.Tk()
    root.title(f"TIFF / BMP 8-bit Grayscale Converter ({VERSION})")
    root.geometry("1400x840")
    root.minsize(1120, 700)

    paned = ttk.PanedWindow(root, orient="horizontal")
    paned.pack(fill="both", expand=True)
    left = ttk.Frame(paned)
    right = ttk.LabelFrame(paned, text="Preview (original vs. 8-bit result)")
    paned.add(left, weight=3)
    paned.add(right, weight=2)

    files = []  # list of paths, parallel to tree rows
    msgq = queue.Queue()
    state = {"color": "#ffffff", "running": False}

    out_dir_var = tk.StringVar()
    suffix_var = tk.StringVar(value="_8bit")
    fmt_var = tk.StringVar(value="TIFF")
    hi_var = tk.StringVar(value=HI_SCALE)
    alpha_var = tk.StringVar(value=ALPHA_WHITE)
    overwrite_var = tk.BooleanVar(value=False)
    recursive_var = tk.BooleanVar(value=False)
    icc_var = tk.StringVar(value=ICC_IGNORE)

    pad = {"padx": 8, "pady": 4}

    # ---- file list
    top = ttk.LabelFrame(left, text="Input files")
    top.pack(fill="both", expand=True, **pad)

    cols = ("name", "format", "mode", "depth", "size", "frames")
    heads = ("File", "Format", "Mode", "Bit depth", "Dimensions", "Frames")
    widths = (300, 70, 70, 120, 110, 60)
    tree = ttk.Treeview(top, columns=cols, show="headings", selectmode="extended", height=9)
    for c, h, w in zip(cols, heads, widths):
        tree.heading(c, text=h)
        tree.column(c, width=w, anchor="w" if c == "name" else "center")
    sb = ttk.Scrollbar(top, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")

    btns = ttk.Frame(left)
    btns.pack(fill="x", **pad)

    def log(text):
        logbox.configure(state="normal")
        logbox.insert("end", text + "\n")
        logbox.see("end")
        logbox.configure(state="disabled")

    def add_paths(paths):
        added = 0
        for p in paths:
            p = os.path.normpath(p)
            if p in files:
                continue
            try:
                info = describe_image(p)
            except Exception as e:
                log(f"SKIP  {os.path.basename(p)}: {e}")
                continue
            files.append(p)
            tree.insert("", "end", iid=p, values=(
                info["name"], info["format"], info["mode"], info["depth"],
                info["size"], info["frames"]))
            added += 1
        if added:
            log(f"Added {added} file(s).")
            schedule_preview(100)

    def add_files():
        paths = filedialog.askopenfilenames(
            title="Select images",
            filetypes=[("TIFF / BMP", "*.tif *.tiff *.bmp"), ("All files", "*.*")])
        add_paths(paths)

    def add_folder():
        d = filedialog.askdirectory(title="Select folder")
        if d:
            add_paths(collect_images(d, recursive_var.get()))

    def remove_selected():
        for iid in tree.selection():
            tree.delete(iid)
            if iid in files:
                files.remove(iid)
        schedule_preview(100)

    def clear_all():
        tree.delete(*tree.get_children())
        files.clear()
        schedule_preview(100)

    ttk.Button(btns, text="Add files...", command=add_files).pack(side="left", padx=2)
    ttk.Button(btns, text="Add folder...", command=add_folder).pack(side="left", padx=2)
    ttk.Checkbutton(btns, text="include subfolders", variable=recursive_var).pack(side="left", padx=6)
    ttk.Button(btns, text="Remove selected", command=remove_selected).pack(side="left", padx=2)
    ttk.Button(btns, text="Clear", command=clear_all).pack(side="left", padx=2)

    # ---- options
    opts = ttk.Frame(left)
    opts.pack(fill="x", **pad)

    outbox = ttk.LabelFrame(opts, text="Output")
    outbox.pack(side="left", fill="both", expand=True, padx=(0, 6))
    ttk.Label(outbox, text="Folder:").grid(row=0, column=0, sticky="w", padx=4, pady=2)
    ttk.Entry(outbox, textvariable=out_dir_var, width=34).grid(row=0, column=1, padx=4, pady=2)

    def pick_out():
        d = filedialog.askdirectory(title="Output folder")
        if d:
            out_dir_var.set(d)

    ttk.Button(outbox, text="Browse...", command=pick_out).grid(row=0, column=2, padx=4)
    ttk.Label(outbox, text="(blank = next to source)").grid(row=1, column=1, sticky="w", padx=4)
    ttk.Label(outbox, text="Suffix:").grid(row=2, column=0, sticky="w", padx=4, pady=2)
    ttk.Entry(outbox, textvariable=suffix_var, width=14).grid(row=2, column=1, sticky="w", padx=4)
    ttk.Label(outbox, text="Format:").grid(row=3, column=0, sticky="w", padx=4, pady=2)
    ff = ttk.Frame(outbox)
    ff.grid(row=3, column=1, sticky="w")
    ttk.Radiobutton(ff, text="TIFF (LZW)", variable=fmt_var, value="TIFF").pack(side="left")
    ttk.Radiobutton(ff, text="BMP", variable=fmt_var, value="BMP").pack(side="left", padx=8)
    ttk.Checkbutton(outbox, text="Overwrite existing (otherwise auto-rename _1, _2...)",
                    variable=overwrite_var).grid(row=4, column=0, columnspan=3, sticky="w", padx=4, pady=2)

    hibox = ttk.LabelFrame(opts, text="16-bit / 32-bit sources")
    hibox.pack(side="left", fill="both", padx=6)
    ttk.Radiobutton(hibox, text="Scale / normalize to 0-255", variable=hi_var,
                    value=HI_SCALE).pack(anchor="w", padx=6, pady=2)
    ttk.Radiobutton(hibox, text="Truncate high bits (16-bit: value >> 8)", variable=hi_var,
                    value=HI_TRUNCATE).pack(anchor="w", padx=6, pady=2)
    ttk.Label(hibox, text="Plain 8-bit grayscale is never altered.",
              foreground="#555").pack(anchor="w", padx=6, pady=(8, 2))

    albox = ttk.LabelFrame(opts, text="Alpha handling (LA / RGBA)")
    albox.pack(side="left", fill="both", padx=(6, 0))
    ttk.Radiobutton(albox, text="Composite onto white (default)", variable=alpha_var,
                    value=ALPHA_WHITE).pack(anchor="w", padx=6, pady=1)
    ttk.Radiobutton(albox, text="Composite onto black", variable=alpha_var,
                    value=ALPHA_BLACK).pack(anchor="w", padx=6, pady=1)
    ttk.Radiobutton(albox, text="Ignore alpha", variable=alpha_var,
                    value=ALPHA_IGNORE).pack(anchor="w", padx=6, pady=1)
    cf = ttk.Frame(albox)
    cf.pack(anchor="w", padx=6, pady=1)
    ttk.Radiobutton(cf, text="Custom color", variable=alpha_var,
                    value=ALPHA_CUSTOM).pack(side="left")
    swatch = tk.Label(cf, width=3, bg=state["color"], relief="sunken")
    swatch.pack(side="left", padx=6)

    def pick_color():
        c = colorchooser.askcolor(color=state["color"], title="Background color")
        if c and c[1]:
            state["color"] = c[1]
            swatch.configure(bg=c[1])
            alpha_var.set(ALPHA_CUSTOM)
            schedule_preview()

    ttk.Button(cf, text="Pick...", command=pick_color).pack(side="left")

    # ---- ICC options
    icc_box = ttk.LabelFrame(left, text="Colour profile (ICC) for RGB sources")
    icc_box.pack(fill="x", **pad)
    ttk.Radiobutton(icc_box, text="Ignore embedded profile - plain luma on the stored values (default)",
                    variable=icc_var, value=ICC_IGNORE).pack(anchor="w", padx=6, pady=1)
    ttk.Radiobutton(icc_box, text="Use embedded profile - true luminance, written as sRGB-encoded gray",
                    variable=icc_var, value=ICC_USE).pack(anchor="w", padx=6, pady=1)
    ttk.Label(icc_box, foreground="#555", wraplength=620, justify="left",
              text="Works with matrix/TRC RGB profiles (sRGB, Adobe RGB, Display P3, ProPhoto, linear) "
                   "and gray profiles. CMYK / LUT-based profiles and files without a profile "
                   "fall back to plain luma (noted in the log).").pack(anchor="w", padx=6, pady=(2, 4))

    # ---- run + log
    runbar = ttk.Frame(left)
    runbar.pack(fill="x", **pad)
    convert_btn = ttk.Button(runbar, text="Convert all")
    convert_btn.pack(side="left")
    progress = ttk.Progressbar(runbar, mode="determinate")
    progress.pack(side="left", fill="x", expand=True, padx=10)

    logframe = ttk.LabelFrame(left, text="Log")
    logframe.pack(fill="both", expand=False, **pad)
    logbox = tk.Text(logframe, height=8, state="disabled", wrap="word")
    lsb = ttk.Scrollbar(logframe, orient="vertical", command=logbox.yview)
    logbox.configure(yscrollcommand=lsb.set)
    logbox.pack(side="left", fill="both", expand=True)
    lsb.pack(side="right", fill="y")

    # ---- preview pane
    pv = {"busy": False, "pending": False, "after_id": None, "token": 0,
          "before": None, "after": None, "tk_before": None, "tk_after": None}
    frame_var = tk.IntVar(value=1)

    pv_top = ttk.Frame(right)
    pv_top.pack(fill="x", padx=6, pady=(4, 0))
    ttk.Label(pv_top, text="Frame:").pack(side="left")
    frame_spin = ttk.Spinbox(pv_top, from_=1, to=1, width=5, textvariable=frame_var,
                             command=lambda: schedule_preview(150))
    frame_spin.pack(side="left", padx=4)
    frame_spin.bind("<Return>", lambda e: schedule_preview(50))
    frame_spin.bind("<FocusOut>", lambda e: schedule_preview(50))
    ttk.Button(pv_top, text="Refresh", command=lambda: schedule_preview(0)).pack(side="left", padx=6)
    pv_info = tk.StringVar(value="Add a file and select it to see a preview.")
    ttk.Label(right, textvariable=pv_info, wraplength=520, justify="left",
              foreground="#444").pack(fill="x", padx=6, pady=(2, 0))
    pv_note = tk.StringVar(value="")
    ttk.Label(right, textvariable=pv_note, wraplength=520, justify="left",
              foreground="#8a5a00").pack(fill="x", padx=6)

    pv_body = ttk.Frame(right)
    pv_body.pack(fill="both", expand=True, padx=6, pady=6)
    pv_body.columnconfigure(0, weight=1)
    pv_body.rowconfigure(1, weight=1)
    pv_body.rowconfigure(3, weight=1)
    ttk.Label(pv_body, text="Original (transparency shown as checkerboard)").grid(row=0, column=0, sticky="w")
    cv_before = tk.Canvas(pv_body, bg="#2b2b2b", highlightthickness=0, width=420, height=200)
    cv_before.grid(row=1, column=0, sticky="nsew", pady=(0, 6))
    ttk.Label(pv_body, text="Result (8-bit grayscale, with current options)").grid(row=2, column=0, sticky="w")
    cv_after = tk.Canvas(pv_body, bg="#2b2b2b", highlightthickness=0, width=420, height=200)
    cv_after.grid(row=3, column=0, sticky="nsew")

    def checker(w, h, cell=8):
        yy, xx = np.indices((h, w))
        v = np.where(((yy // cell) + (xx // cell)) % 2 == 0, 255, 200).astype(np.uint8)
        return Image.fromarray(v, "L").convert("RGBA")

    def show(canvas, img, key):
        canvas.delete("all")
        if img is None:
            return
        cw, ch = max(canvas.winfo_width(), 60), max(canvas.winfo_height(), 60)
        w, h = img.size
        s = min(cw / w, ch / h)
        nw, nh = max(1, int(w * s)), max(1, int(h * s))
        disp = img.resize((nw, nh), Image.BILINEAR if s < 1 else Image.NEAREST)
        if disp.mode == "RGBA":
            disp = Image.alpha_composite(checker(nw, nh), disp).convert("RGB")
        pv[key] = ImageTk.PhotoImage(disp)
        canvas.create_image(cw // 2, ch // 2, image=pv[key])

    def redraw(*_):
        show(cv_before, pv["before"], "tk_before")
        show(cv_after, pv["after"], "tk_after")

    cv_before.bind("<Configure>", lambda e: root.after_idle(redraw))
    cv_after.bind("<Configure>", lambda e: root.after_idle(redraw))

    def selected_path():
        sel = [s for s in tree.selection() if s in files]
        if sel:
            return sel[0]
        return files[0] if files else None

    def clear_preview(msg):
        pv["before"] = pv["after"] = None
        pv_info.set(msg)
        pv_note.set("")
        redraw()

    def run_preview():
        pv["after_id"] = None
        path = selected_path()
        if not path:
            clear_preview("Add a file and select it to see a preview.")
            return
        if pv["busy"]:
            pv["pending"] = True
            return
        try:
            fi = max(int(frame_var.get()) - 1, 0)
        except (tk.TclError, ValueError):
            fi = 0
        args = (path, fi, alpha_var.get(), state["color"], hi_var.get(), icc_var.get())
        pv["busy"] = True
        pv["token"] += 1
        token = pv["token"]
        pv_info.set("Rendering preview...")

        def work():
            try:
                msgq.put(("preview", (token, path, build_preview(*args))))
            except Exception as e:
                msgq.put(("preview_err", (token, f"Preview failed: {e}")))

        threading.Thread(target=work, daemon=True).start()

    def schedule_preview(delay=250):
        if pv["after_id"] is not None:
            root.after_cancel(pv["after_id"])
        pv["after_id"] = root.after(delay, run_preview)

    def on_preview_result(token, path, res):
        pv["busy"] = False
        if pv["pending"]:
            pv["pending"] = False
            run_preview()
            return
        if path != selected_path():
            schedule_preview(0)
            return
        pv["before"], pv["after"] = res["before"], res["after"]
        frame_spin.configure(to=max(res["n_frames"], 1))
        pv_info.set(res["info"])
        pv_note.set("; ".join(res["notes"]))
        redraw()

    def on_select(_event=None):
        frame_var.set(1)
        schedule_preview(100)

    tree.bind("<<TreeviewSelect>>", on_select)
    for _v in (fmt_var, hi_var, alpha_var, icc_var):
        _v.trace_add("write", lambda *a: schedule_preview(150))

    def worker(jobs, settings):
        ok = fail = 0
        for i, src in enumerate(jobs, 1):
            try:
                r = convert_file(src, **settings)
                extra = f"  [{r['note']}]" if r["note"] else ""
                msgq.put(("log", f"OK    {os.path.basename(src)} -> {r['dest']} "
                                 f"({r['frames_out']} frame(s), read via {r['reader']}){extra}"))
                ok += 1
            except Exception as e:
                msgq.put(("log", f"FAIL  {os.path.basename(src)}: {e}"))
                fail += 1
            msgq.put(("progress", i))
        msgq.put(("done", (ok, fail)))

    def start():
        if state["running"]:
            return
        if not files:
            messagebox.showinfo("Nothing to do", "Add some files first.")
            return
        out_dir = out_dir_var.get().strip()
        if out_dir:
            try:
                os.makedirs(out_dir, exist_ok=True)
            except OSError as e:
                messagebox.showerror("Output folder", str(e))
                return
        settings = dict(
            out_dir=out_dir, suffix=suffix_var.get(), out_fmt=fmt_var.get(),
            alpha_mode=alpha_var.get(), custom_color=state["color"],
            hi_mode=hi_var.get(), overwrite=overwrite_var.get(), icc_mode=icc_var.get())
        state["running"] = True
        convert_btn.configure(state="disabled")
        progress.configure(maximum=len(files), value=0)
        log(f"--- Converting {len(files)} file(s) -> {settings['out_fmt']}, "
            f"alpha={settings['alpha_mode']}, 16-bit={settings['hi_mode']}, "
            f"ICC={settings['icc_mode']} ---")
        threading.Thread(target=worker, args=(list(files), settings), daemon=True).start()

    convert_btn.configure(command=start)

    def poll():
        try:
            while True:
                kind, val = msgq.get_nowait()
                if kind == "log":
                    log(val)
                elif kind == "progress":
                    progress.configure(value=val)
                elif kind == "preview":
                    on_preview_result(*val)
                elif kind == "preview_err":
                    pv["busy"] = False
                    if pv["pending"]:
                        pv["pending"] = False
                        run_preview()
                    else:
                        clear_preview(val[1])
                elif kind == "done":
                    ok, fail = val
                    log(f"--- Done: {ok} succeeded, {fail} failed ---")
                    state["running"] = False
                    convert_btn.configure(state="normal")
        except queue.Empty:
            pass
        root.after(100, poll)

    poll()
    log(f"Ready ({VERSION}). Alpha-aware; 8-bit grayscale preserved exactly.")
    if test_hook is not None:
        ns = dict(locals())
        root.after(300, lambda: test_hook(root, ns))
    root.mainloop()


if __name__ == "__main__":
    run_gui()
