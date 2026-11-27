# TIFF / BMP 8-Bit Grayscale Converter (PWA & Desktop)

[![WebAssembly](https://img.shields.io/badge/WebAssembly-Pyodide-blue.svg)](https://pyodide.org/)
[![PWA](https://img.shields.io/badge/PWA-Offline--Ready-success.svg)](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps)
[![GitHub Pages](https://img.shields.io/badge/GitHub%20Pages-Live%20Demo-brightgreen.svg)](#live-demo--github-pages)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)

A high-precision image conversion tool designed to convert 16-bit, 32-bit int, 32-bit float, RGB, RGBA, CMYK, planar, and multi-page TIFF images (as well as BMP files) into true 8-bit grayscale images.

Available as both a **standalone desktop application** (Tkinter) and a **zero-server WebAssembly Progressive Web App (PWA)** hosted on GitHub Pages that runs 100% locally and offline in the user's browser.

---

## Key Features

- **100% Client-Side & Private**: In the browser, files never leave your computer. Conversion runs locally inside WebAssembly (via Pyodide with NumPy and Pillow).
- **Offline Capable (PWA)**: Includes a Service Worker and Web App Manifest; install it as a standalone desktop app in Chrome, Edge, or Safari and run completely offline without an internet connection.
- **Deep Format & Precision Support**:
  - 8-bit, 16-bit, and 32-bit integer and 32-bit float images.
  - Planar (`separate`) and contiguous layouts.
  - Multi-page TIFFs preserved in output (with LZW compression).
  - BMP fallback and conversion support.
- **Tone Curve & Bit-Depth Mapping**:
  - **Scale**: Normalize dynamic range (`min-max` to `0-255`).
  - **Truncate**: Bit-shift preserving absolute high bits (16-bit: `value >> 8`).
  - **Pure 8-bit passthrough**: Plain 8-bit grayscale inputs are preserved untouched.
- **Alpha Transparency Handling**:
  - Composite transparent pixels onto white (default), black, or any custom hex/RGB color.
  - Option to ignore alpha channel.
  - Proper un-premultiplication for associated alpha images.
- **Colorimetry & Embedded ICC Profiles**:
  - Parses matrix/TRC RGB profiles (sRGB, Adobe RGB, Display P3, ProPhoto, linear) and Gray TRC curves.
  - Computes true colorimetric luminance encoded into sRGB 8-bit grayscale.
- **Side-by-Side Live Preview**:
  - Visual inspection of original source vs. 8-bit converted result.
  - Multi-frame selector for multi-page TIFFs.
  - Checkerboard pattern to visualize transparency.
- **Flexible Batch Export**:
  - Download all converted images in a single `.zip` archive.
  - Individual download buttons per row.

---

## Live Demo & GitHub Pages

Once deployed to your GitHub repository, the web app is accessible at:
```
https://<your-username>.github.io/<your-repo-name>/
```

### Enabling GitHub Pages in Your Repository:
1. Push this repository to GitHub.
2. In your repository on GitHub, navigate to **Settings** > **Pages**.
3. Under **Build and deployment** > **Source**, select **GitHub Actions**.
4. The automated workflow in `.github/workflows/deploy.yml` will automatically build and publish the app!

---

## Local Development & Testing

### 1. Running the Web App Locally

You can test the static web app locally using Python's built-in HTTP server:

```bash
# Navigate to the web folder and start a local server
cd web
python -m http.server 8000
```

Open `http://localhost:8000` in any modern browser (Chrome, Edge, Firefox, Safari).

### 2. Running the Desktop Application

The native desktop Tkinter interface is powered by `tiff_8bit_converter.py`:

```bash
# Install dependencies
pip install -r requirements.txt

# Launch desktop GUI
python tiff_8bit_converter.py
```

### 3. Running Automated Tests

Run the test suite to verify conversion accuracy across all formats and modes:

```bash
python test_converter.py
python test_tifffile.py
python test_icc_preview.py
```

---

## Project Structure

```
Tiff_8Bit_Converter_PWA_V1/
├── .github/
│   └── workflows/
│       └── deploy.yml          # GitHub Pages deployment workflow
├── web/                        # Static files deployed to GitHub Pages
│   ├── index.html              # Modern responsive PWA user interface
│   ├── style.css               # Styling and dark theme
│   ├── app.js                  # Client application logic & UI orchestration
│   ├── worker.js               # Web Worker executing Pyodide WASM
│   ├── converter_core.py       # Core conversion engine running in WASM
│   ├── manifest.json           # Progressive Web App manifest
│   ├── sw.js                   # Service Worker for offline asset caching
│   ├── vendor/
│   │   └── jszip.min.js        # Vendored JSZip for offline batch ZIP export
│   └── icons/
│       ├── icon.svg            # App icon (vector)
│       ├── icon-192.png     # App icon (192x192 PNG)
│       ├── icon-512.png     # App icon (512x512 PNG)
│       ├── icon-maskable-512.png  # Maskable app icon (512x512 PNG)
│       └── apple-touch-icon-180.png  # iOS home screen icon
├── converter_core.py           # Shared conversion engine (used by desktop app)
├── tiff_8bit_converter.py      # Desktop Tkinter GUI application
├── test_converter.py           # Core conversion test suite
├── test_tifffile.py            # High bit-depth & tifffile test suite
├── test_icc_preview.py         # ICC profile & preview test suite
├── requirements.txt            # Desktop Python dependencies
├── .gitignore                  # Git ignore rules
└── README.md                   # Repository documentation
```

---

## License

This project is licensed under the [GNU Affero General Public License v3.0 (AGPL-3.0)](LICENSE).
