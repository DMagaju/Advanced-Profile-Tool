# __author__  = "Dipendra Magaju"
# __licence__ = "GNU General Public License v2 or later (GPLv2+)"

"""
profile_line_tool.py — Custom QGIS map tool for polyline digitizing.

Left-click  : add vertex  (emits vertex_added with the current partial geometry)
Right-click : finalise and emit line_completed
Double-click: finalise and emit line_completed
"""

from qgis.PyQt.QtCore import pyqtSignal, Qt
from qgis.PyQt.QtGui import QColor
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.core import Qgis, QgsWkbTypes, QgsGeometry, QgsPointXY

# ---------------------------------------------------------------------------
# Geometry-type constants — Qgis.GeometryType (3.36+) with QgsWkbTypes fallback
# ---------------------------------------------------------------------------
try:
    _LINE_GEOM  = Qgis.GeometryType.Line
    _POINT_GEOM = Qgis.GeometryType.Point
except AttributeError:
    _LINE_GEOM  = QgsWkbTypes.LineGeometry   # type: ignore[attr-defined]
    _POINT_GEOM = QgsWkbTypes.PointGeometry  # type: ignore[attr-defined]

try:
    _ICON_CIRCLE = QgsRubberBand.IconType.ICON_CIRCLE
except AttributeError:
    _ICON_CIRCLE = QgsRubberBand.ICON_CIRCLE  # type: ignore[attr-defined]

try:
    _LEFT  = Qt.MouseButton.LeftButton
    _RIGHT = Qt.MouseButton.RightButton
    _DASH  = Qt.PenStyle.DashLine
except AttributeError:
    _LEFT  = Qt.LeftButton   # type: ignore[attr-defined]
    _RIGHT = Qt.RightButton  # type: ignore[attr-defined]
    _DASH  = Qt.DashLine     # type: ignore[attr-defined]


class ProfileLineTool(QgsMapTool):
    """Rubber-band polyline digitizer.

    Signals
    -------
    vertex_added(QgsGeometry)
        Fired after every left-click once >= 2 vertices exist.
        Carries the current partial polyline — used for live chart preview.
    line_completed(QgsGeometry)
        Fired on right-click or double-click with the finalised polyline.
    """

    line_completed = pyqtSignal(object)  # QgsGeometry
    vertex_added   = pyqtSignal(object)  # QgsGeometry  (partial, >= 2 pts)

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self._points = []

        self._draw_band = QgsRubberBand(canvas, _LINE_GEOM)
        self._draw_band.setColor(QColor(33, 150, 243, 200))
        self._draw_band.setWidth(2)

        self._temp_band = QgsRubberBand(canvas, _LINE_GEOM)
        self._temp_band.setColor(QColor(33, 150, 243, 100))
        self._temp_band.setWidth(1)
        self._temp_band.setLineStyle(_DASH)

        self._vertex_band = QgsRubberBand(canvas, _POINT_GEOM)
        self._vertex_band.setColor(QColor(255, 87, 34))
        self._vertex_band.setIcon(_ICON_CIRCLE)
        self._vertex_band.setIconSize(8)

    def reset(self):
        self._points = []
        self._draw_band.reset(_LINE_GEOM)
        self._temp_band.reset(_LINE_GEOM)
        self._vertex_band.reset(_POINT_GEOM)

    def canvasPressEvent(self, event):
        if event.button() == _LEFT:
            pt = QgsPointXY(self.toMapCoordinates(event.pos()))
            self._points.append(pt)
            self._draw_band.addPoint(pt)
            self._vertex_band.addPoint(pt)
            # Live preview: emit partial geometry once we have a valid line
            if len(self._points) >= 2:
                self.vertex_added.emit(QgsGeometry.fromPolylineXY(self._points))
        elif event.button() == _RIGHT:
            self._finalise()

    def canvasDoubleClickEvent(self, event):
        if event.button() == _LEFT:
            self._finalise()

    def canvasMoveEvent(self, event):
        if not self._points:
            return
        pt = QgsPointXY(self.toMapCoordinates(event.pos()))
        self._temp_band.reset(_LINE_GEOM)
        self._temp_band.addPoint(self._points[-1])
        self._temp_band.addPoint(pt)

    def _finalise(self):
        self._temp_band.reset(_LINE_GEOM)
        if len(self._points) >= 2:
            geom = QgsGeometry.fromPolylineXY(self._points)
            self.line_completed.emit(geom)
        self.reset()

    def deactivate(self):
        self._temp_band.reset(_LINE_GEOM)
        super().deactivate()

    def isEditTool(self):
        return False

    def isTransient(self):
        return False
