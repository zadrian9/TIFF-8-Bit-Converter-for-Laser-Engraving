/**
 * TIFF 8-Bit Converter - WebAssembly Web Worker
 * Powered by Pyodide (CPython in WebAssembly + NumPy + Pillow + tifffile)
 */

let pyodide = null;
let isReady = false;

function postStatus(status, detail = "") {
    self.postMessage({ type: "status", status, detail });
}

function postLog(text, level = "info") {
    self.postMessage({ type: "log", text, level });
}

async function initPyodide() {
    try {
        postStatus("loading_runtime", "Loading WebAssembly Python runtime (Pyodide)...");
        importScripts("https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js");

        pyodide = await loadPyodide({
            indexURL: "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/"
        });

        postStatus("loading_packages", "Loading NumPy and Pillow WebAssembly wheels...");
        await pyodide.loadPackage(["numpy", "pillow"]);

        postStatus("loading_tifffile", "Installing tifffile package...");
        await pyodide.loadPackage("micropip");
        await pyodide.runPythonAsync(`
import micropip
try:
    await micropip.install("tifffile<=2024.5.22")
except Exception:
    try:
        await micropip.install("tifffile", deps=False)
    except Exception as e:
        print("Notice: tifffile fallback to Pillow:", e)
`);

        postStatus("loading_engine", "Loading image converter engine...");
        const response = await fetch("converter_core.py");
        if (!response.ok) {
            throw new Error(`Failed to fetch converter_core.py: HTTP ${response.status}`);
        }
        const coreCode = await response.text();
        await pyodide.runPythonAsync(coreCode);

        // Ensure temp directory exists in Pyodide virtual filesystem
        pyodide.FS.mkdirTree("/tmp");

        isReady = true;
        postStatus("ready", "WebAssembly engine ready (offline-capable).");
        postLog("WebAssembly TIFF conversion engine initialized successfully.", "success");
    } catch (err) {
        postStatus("error", err.message || String(err));
        postLog(`Engine initialization failed: ${err.message || String(err)}`, "error");
    }
}

// Start initialization immediately when worker is spawned
initPyodide();

self.onmessage = async (event) => {
    const { id, action, payload } = event.data;

    if (!isReady) {
        self.postMessage({
            id,
            action,
            success: false,
            error: "WebAssembly engine is still initializing. Please wait a moment..."
        });
        return;
    }

    try {
        if (action === "describe") {
            const { filename, buffer } = payload;
            const path = `/tmp/${id}_${filename}`;
            pyodide.FS.writeFile(path, new Uint8Array(buffer));

            const descFunc = pyodide.globals.get("web_describe_path");
            const pyRes = descFunc(path);
            const result = pyRes.toJs({ dict_converter: Object.fromEntries });

            try { pyodide.FS.unlink(path); } catch (e) {}

            self.postMessage({ id, action, success: true, result });
        } else if (action === "preview") {
            const { filename, buffer, frameIndex, alphaMode, customColor, hiMode, iccMode } = payload;
            const path = `/tmp/${id}_${filename}`;
            pyodide.FS.writeFile(path, new Uint8Array(buffer));

            const prevFunc = pyodide.globals.get("web_preview_path");
            const pyRes = prevFunc(
                path,
                frameIndex || 0,
                alphaMode || "white",
                customColor || "#ffffff",
                hiMode || "scale",
                iccMode || "ignore",
                1200
            );
            const result = pyRes.toJs({ dict_converter: Object.fromEntries });

            try { pyodide.FS.unlink(path); } catch (e) {}

            self.postMessage({ id, action, success: true, result });
        } else if (action === "convert") {
            const { filename, buffer, outFmt, alphaMode, customColor, hiMode, iccMode, suffix } = payload;
            const path = `/tmp/${id}_${filename}`;
            pyodide.FS.writeFile(path, new Uint8Array(buffer));

            const convFunc = pyodide.globals.get("web_convert_path");
            const pyRes = convFunc(
                path,
                outFmt || "TIFF",
                alphaMode || "white",
                customColor || "#ffffff",
                hiMode || "scale",
                iccMode || "ignore",
                suffix || "_8bit"
            );

            // In pyRes: 'bytes' is a Python bytes object
            const outBytes = pyRes.get("bytes").toJs();
            const outFilename = pyRes.get("filename");
            const framesIn = pyRes.get("frames_in");
            const framesOut = pyRes.get("frames_out");
            const note = pyRes.get("note");
            const reader = pyRes.get("reader");
            const sizeBytes = pyRes.get("size_bytes");

            try { pyodide.FS.unlink(path); } catch (e) {}

            self.postMessage(
                {
                    id,
                    action,
                    success: true,
                    result: {
                        filename: outFilename,
                        bytes: outBytes,
                        framesIn,
                        framesOut,
                        note,
                        reader,
                        sizeBytes
                    }
                },
                [outBytes.buffer] // Zero-copy transfer
            );
        } else {
            throw new Error(`Unknown worker action: ${action}`);
        }
    } catch (err) {
        self.postMessage({
            id,
            action,
            success: false,
            error: err.message || String(err)
        });
    }
};
