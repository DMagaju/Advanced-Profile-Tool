# Advanced Profile Tool

A QGIS plugin for extracting and visualising elevation and attribute profiles along a user-defined alignment, with cross-section, annotation, and export capabilities. Built for water engineers and flood modellers.

## Features

- Draw a profile line interactively on the map canvas — or select an existing single-line layer
- Up to **3 independent profile windows** with per-window layer assignment, Y-axis labels, and cut/fill shading
- **Cross-section dialog** mirroring the profile layout (same windows, same data, live sync)
- Perpendicular XS markers on the map with name labels
- **Sketch annotation tools** — pen, line, arrow, text, level marker, rectangle, ellipse; Ctrl+click polyline mode
- Per-layer line width, opacity, and colour controls
- Configurable map line style (colour, width, opacity)
- **Chainage cursor** tool
- **CSV and PNG export**
- Supports both **raster DEMs** and **vector layers** as profile sources

## Requirements

- QGIS 3.28 or later
- matplotlib and numpy (included in standard QGIS / OSGeo4W installation; chart panel unavailable without them)

## Installation

### From QGIS Plugin Manager (recommended)
1. Open QGIS → **Plugins → Manage and Install Plugins**
2. Search for **Advanced Profile Tool**
3. Click **Install**

### From ZIP
1. Download the latest ZIP from [Releases](https://github.com/DMagaju/Advanced-Profile-Tool/releases)
2. In QGIS → **Plugins → Manage and Install Plugins → Install from ZIP**

## Usage

1. Activate the plugin from **Plugins → Advanced Profile Tool → Advanced Profile Tool**, or click the toolbar button
2. Click **Draw Profile Line** to digitise a profile alignment on the canvas, or select an existing line layer from the **From layer** combo and click **Use**
3. Add raster or vector layers to any of the three profile windows using the layer controls
4. Use the cross-section button to open perpendicular XS views at any chainage
5. Annotate using the sketch tools, then export to CSV or PNG

## License

GNU General Public License v2 or later (GPLv2+) — see [LICENSE](LICENSE)

## Author

Dipendra Magaju — [dipen.magaju@gmail.com](mailto:dipen.magaju@gmail.com)

## Repository

[https://github.com/DMagaju/Advanced-Profile-Tool](https://github.com/DMagaju/Advanced-Profile-Tool)

## Issues

[https://github.com/DMagaju/Advanced-Profile-Tool/issues](https://github.com/DMagaju/Advanced-Profile-Tool/issues)
