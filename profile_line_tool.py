# __author__  = "Dipendra Magaju"
# __licence__ = "GNU General Public License v2 or later (GPLv2+)"

"""
profile_line_tool.py — Custom QGIS map tool for polyline digitizing.

Left-click  : add vertex  (emits vertex_added with the current partial geometry)
Right-click : finalise and emit line_completed
Double-click: finalise and emit line_completed
Ctrl+Z      : remove the most-recently-added vertex (undo one click)
"""

import math

from qgis.PyQt.QtCore import pyqtSignal, Qt
from qgis.PyQt.QtGui import QColor, QCursor
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.core import (
    Qgis, QgsWkbTypes, QgsGeometry, QgsPointXY, QgsRectangle, QgsFeatureRequest,
)

# ---------------------------------------------------------------------------
# Geometry-type constants — Qgis.GeometryType (3.36+) with QgsWkbTypes fallback
# ---------------------------------------------------------------------------
try:
    _LINE_GEOM    = Qgis.GeometryType.Line
    _POINT_GEOM   = Qgis.GeometryType.Point
    _POLYGON_GEOM = Qgis.GeometryType.Polygon
except AttributeError:
    _LINE_GEOM    = QgsWkbTypes.LineGeometry    # type: ignore[attr-defined]
    _POINT_GEOM   = QgsWkbTypes.PointGeometry   # type: ignore[attr-defined]
    _POLYGON_GEOM = QgsWkbTypes.PolygonGeometry # type: ignore[attr-defined]

try:
    _ICON_CIRCLE = QgsRubberBand.IconType.ICON_CIRCLE
    _ICON_CROSS  = QgsRubberBand.IconType.ICON_CROSS
except AttributeError:
    _ICON_CIRCLE = QgsRubberBand.ICON_CIRCLE  # type: ignore[attr-defined]
    _ICON_CROSS  = QgsRubberBand.ICON_CROSS   # type: ignore[attr-defined]

try:
    _LEFT  = Qt.MouseButton.LeftButton
    _RIGHT = Qt.MouseButton.RightButton
    _DASH  = Qt.PenStyle.DashLine
except AttributeError:
    _LEFT  = Qt.LeftButton   # type: ignore[attr-defined]
    _RIGHT = Qt.RightButton  # type: ignore[attr-defined]
    _DASH  = Qt.DashLine     # type: ignore[attr-defined]

try:
    _CTRL_MOD = Qt.KeyboardModifier.ControlModifier
except AttributeError:
    _CTRL_MOD = Qt.ControlModifier  # type: ignore[attr-defined]

try:
    _POINTING_HAND_CURSOR = Qt.CursorShape.PointingHandCursor
    _CROSS_CURSOR         = Qt.CursorShape.CrossCursor
except AttributeError:
    _POINTING_HAND_CURSOR = Qt.PointingHandCursor  # type: ignore[attr-defined]
    _CROSS_CURSOR         = Qt.CrossCursor         # type: ignore[attr-defined]

try:
    _KEY_Z = Qt.Key.Key_Z
except AttributeError:
    _KEY_Z = Qt.Key_Z  # type: ignore[attr-defined]


class ProfileLineTool(QgsMapTool):
    """Rubber-band polyline digitizer.

    Signals
    -------
    vertex_added(QgsGeometry)
        Fired after every left-click once >= 2 vertices exist.
        Carries the current partial polyline — used for live chart preview.
    line_completed(QgsGeometry)
        Fired on right-click or double-click with the finalised polyline.

    Ctrl+Z while still digitizing (map canvas has focus, nothing finalised
    yet) removes the most-recently-added vertex — same shortcut as an
    undo anywhere else, rather than having to scrap the whole line and
    start over for one misplaced click. Shared by both the Layer Profile
    tab's own 'Draw Line' and Modify Terrain's Horizontal Alignment, since
    both use this same tool class.
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

    def keyPressEvent(self, event):
        is_ctrl = bool(event.modifiers() & _CTRL_MOD)
        if is_ctrl and event.key() == _KEY_Z:
            self._undo_last_point()
            event.accept()
            return
        super().keyPressEvent(event)

    def _undo_last_point(self):
        """Removes the most-recently-added vertex and rebuilds both
        rubber bands from what's left — used by Ctrl+Z (see
        keyPressEvent) rather than forcing a full reset/restart for one
        misplaced click."""
        if not self._points:
            return
        self._points.pop()
        self._draw_band.reset(_LINE_GEOM)
        self._vertex_band.reset(_POINT_GEOM)
        for p in self._points:
            self._draw_band.addPoint(p)
            self._vertex_band.addPoint(p)
        if len(self._points) >= 2:
            self.vertex_added.emit(QgsGeometry.fromPolylineXY(self._points))
        self._canvas.refresh()

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


class VertexSelectTool(QgsMapTool):
    """Click/drag/right-click map tool for editing a known list of points
    (e.g. Modify Terrain's horizontal alignment vertices) — reports what
    happened by index into whatever list set_vertices() was last given;
    it never edits that list itself or knows what the points MEAN (not
    even which ones are the alignment's own two ends — the dock decides
    what reprojecting chainage-anchored break points/control sections
    onto an edited alignment means, same 'dumb map tool, dock owns the
    logic' division as ProfileLineTool.

    - Left-click a vertex (no drag): reports it via vertex_clicked, same
      as before (e.g. to show/set its fillet radius).
    - Left-click-drag a vertex: reports the new position via vertex_moved
      once released. A thin dashed preview line to its immediate
      neighbours follows the drag so the shape reads clearly without the
      cost of re-fillet/re-extracting on every mouse-move tick — the
      dock only does the real (potentially expensive — ground re-sample)
      rebuild once, on release.
    - Right-click a vertex: reports it via vertex_deleted.

    Signals
    -------
    vertex_clicked(object)
        int index into the current vertex list, or None if nothing was
        within tolerance of a plain (non-dragged) left-click.
    vertex_moved(int, object)
        (index, QgsPointXY) — a vertex was dragged to a new position and
        the mouse released.
    vertex_deleted(int)
        index of a vertex that was right-clicked.
    """

    vertex_clicked = pyqtSignal(object)
    vertex_moved   = pyqtSignal(int, object)
    vertex_deleted = pyqtSignal(int)

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self._points = []
        self._click_tol_px = 10
        self.setCursor(QCursor(_POINTING_HAND_CURSOR))

        self._drag_idx  = None
        self._dragged    = False
        self._drag_band = QgsRubberBand(canvas, _POINT_GEOM)
        self._drag_band.setColor(QColor(255, 235, 59, 230))
        self._drag_band.setIcon(_ICON_CIRCLE)
        self._drag_band.setIconSize(12)

        self._drag_temp_band = QgsRubberBand(canvas, _LINE_GEOM)
        self._drag_temp_band.setColor(QColor(255, 235, 59, 200))
        self._drag_temp_band.setWidth(2)
        self._drag_temp_band.setLineStyle(_DASH)

    def set_vertices(self, points):
        self._points = list(points)

    def _nearest_vertex(self, click_pt):
        mupp = self._canvas.mapUnitsPerPixel()
        tol = self._click_tol_px * mupp
        best_i, best_d = None, tol
        for i, p in enumerate(self._points):
            d = math.hypot(p.x() - click_pt.x(), p.y() - click_pt.y())
            if d <= tol and d < best_d:
                best_d, best_i = d, i
        return best_i

    def canvasPressEvent(self, event):
        click_pt = self.toMapCoordinates(event.pos())
        idx = self._nearest_vertex(click_pt)
        if event.button() == _RIGHT:
            if idx is not None:
                self.vertex_deleted.emit(idx)
            return
        if event.button() != _LEFT:
            return
        self.vertex_clicked.emit(idx)
        if idx is not None:
            self._drag_idx  = idx
            self._dragged    = False

    def canvasMoveEvent(self, event):
        if self._drag_idx is None:
            return
        pt = QgsPointXY(self.toMapCoordinates(event.pos()))
        self._dragged = True
        self._drag_band.reset(_POINT_GEOM)
        self._drag_band.addPoint(pt)
        seg_pts = []
        if self._drag_idx > 0:
            seg_pts.append(self._points[self._drag_idx - 1])
        seg_pts.append(pt)
        if self._drag_idx < len(self._points) - 1:
            seg_pts.append(self._points[self._drag_idx + 1])
        self._drag_temp_band.reset(_LINE_GEOM)
        if len(seg_pts) >= 2:
            self._drag_temp_band.setToGeometry(QgsGeometry.fromPolylineXY(seg_pts), None)

    def canvasReleaseEvent(self, event):
        if event.button() != _LEFT or self._drag_idx is None:
            return
        idx, moved = self._drag_idx, self._dragged
        self._drag_idx = None
        self._dragged   = False
        self._drag_band.reset(_POINT_GEOM)
        self._drag_temp_band.reset(_LINE_GEOM)
        if moved:
            pt = QgsPointXY(self.toMapCoordinates(event.pos()))
            self.vertex_moved.emit(idx, pt)

    def reset(self):
        self._drag_idx = None
        self._dragged   = False
        self._drag_band.reset(_POINT_GEOM)
        self._drag_temp_band.reset(_LINE_GEOM)

    def isEditTool(self):
        return False

    def isTransient(self):
        return False


class ShapeDrawTool(QgsMapTool):
    """Map tool for Shapes and Polygon Surface — draws one of a handful of
    regular shapes, or a freehand closed polygon, in a single tool rather
    than a separate QgsMapTool subclass per shape (switched via
    set_mode(), same idea as how one FlowLayout instance serves every
    button row rather than a bespoke layout per row).

    Circle / Ellipse / Rectangle / Square — two workflows:
    - Freehand (default, no fixed size set): click-drag-release — press
      sets the anchor point, dragging previews the shape live, release
      finalises it. Circle/Ellipse are anchored at their CENTRE (drag
      sets the radius/radii); Rectangle/Square are anchored at one
      CORNER (drag sets the opposite corner — Square keeps the drag
      square by taking the larger of dx/dy for both sides).
    - Fixed size (set_fixed_size(params) given a non-empty dict): no
      drag needed — a preview of the exact size/shape follows the
      cursor, and a single click commits it right there. Circle/Ellipse
      are still centred on the click; Rectangle/Square instead treat the
      click as their ORIGIN corner, extending +X (width) / +Y (length),
      per explicit request.

    Polygon: click-click-...-right-click or double-click to finish, same
    convention as ProfileLineTool, just closed into a ring on finish
    instead of left open as a line.

    Signals
    -------
    shape_completed(QgsGeometry)
        A finished, valid (non-zero-area) polygon.
    """

    shape_completed = pyqtSignal(object)  # QgsGeometry

    _SEGMENTS = 72  # vertices used to approximate a circle/ellipse

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self._mode = 'circle'
        self._anchor = None
        self._points = []   # polygon mode only
        self._fixed_size = None   # dict, or None for freehand — see set_fixed_size

        self._band = QgsRubberBand(canvas, _POLYGON_GEOM)
        self._band.setColor(QColor(233, 30, 99, 60))
        self._band.setStrokeColor(QColor(233, 30, 99, 220))
        self._band.setWidth(2)

        self._vertex_band = QgsRubberBand(canvas, _POINT_GEOM)
        self._vertex_band.setColor(QColor(233, 30, 99, 220))
        self._vertex_band.setIcon(_ICON_CIRCLE)
        self._vertex_band.setIconSize(8)

    def set_mode(self, mode):
        """mode: 'circle' | 'ellipse' | 'rectangle' | 'square' | 'polygon'."""
        self._mode = mode
        self.reset()

    def set_fixed_size(self, params):
        """params, per mode (or None to go back to freehand drag):
        circle {'diameter'}; ellipse {'width','height'}; rectangle
        {'width','length'}; square {'side'}. Switches this tool straight
        to the 'preview follows the cursor, click to place' workflow —
        see the class docstring."""
        self._fixed_size = params
        self._band.reset(_POLYGON_GEOM)

    def reset(self):
        self._anchor = None
        self._points = []
        self._band.reset(_POLYGON_GEOM)
        self._vertex_band.reset(_POINT_GEOM)

    def _shape_geom(self, p0, p1, fixed=None):
        """The live/final shape geometry anchored at p0 — sized/shaped
        toward p1 when fixed is None (freehand), or to the given fixed
        params when it isn't (p1 is unused then). None while a freehand
        drag is still degenerate (zero size)."""
        if self._mode == 'circle':
            if fixed:
                r = fixed['diameter'] / 2.0
            else:
                r = math.hypot(p1.x() - p0.x(), p1.y() - p0.y())
            if r <= 1e-9:
                return None
            return QgsGeometry.fromPointXY(p0).buffer(r, self._SEGMENTS)
        if self._mode == 'ellipse':
            if fixed:
                rx, ry = fixed['width'] / 2.0, fixed['height'] / 2.0
            else:
                rx, ry = abs(p1.x() - p0.x()), abs(p1.y() - p0.y())
            if rx <= 1e-9 or ry <= 1e-9:
                return None
            n = self._SEGMENTS
            pts = [QgsPointXY(p0.x() + rx * math.cos(2 * math.pi * i / n),
                               p0.y() + ry * math.sin(2 * math.pi * i / n))
                   for i in range(n)]
            return QgsGeometry.fromPolygonXY([pts])
        if self._mode == 'rectangle':
            if fixed:
                x0, y0 = p0.x(), p0.y()
                x1, y1 = x0 + fixed['width'], y0 + fixed['length']
            else:
                x0, x1 = sorted((p0.x(), p1.x()))
                y0, y1 = sorted((p0.y(), p1.y()))
            if abs(x1 - x0) <= 1e-9 or abs(y1 - y0) <= 1e-9:
                return None
            pts = [QgsPointXY(x0, y0), QgsPointXY(x1, y0),
                   QgsPointXY(x1, y1), QgsPointXY(x0, y1)]
            return QgsGeometry.fromPolygonXY([pts])
        if self._mode == 'square':
            if fixed:
                x0, y0 = p0.x(), p0.y()
                x1, y1 = x0 + fixed['side'], y0 + fixed['side']
            else:
                s = max(abs(p1.x() - p0.x()), abs(p1.y() - p0.y()))
                if s <= 1e-9:
                    return None
                sx = s if p1.x() >= p0.x() else -s
                sy = s if p1.y() >= p0.y() else -s
                x0, x1 = sorted((p0.x(), p0.x() + sx))
                y0, y1 = sorted((p0.y(), p0.y() + sy))
            if abs(x1 - x0) <= 1e-9 or abs(y1 - y0) <= 1e-9:
                return None
            pts = [QgsPointXY(x0, y0), QgsPointXY(x1, y0),
                   QgsPointXY(x1, y1), QgsPointXY(x0, y1)]
            return QgsGeometry.fromPolygonXY([pts])
        return None

    def canvasPressEvent(self, event):
        if self._mode == 'polygon':
            if event.button() == _LEFT:
                pt = QgsPointXY(self.toMapCoordinates(event.pos()))
                self._points.append(pt)
                self._vertex_band.addPoint(pt)
                if len(self._points) >= 3:
                    self._band.setToGeometry(QgsGeometry.fromPolygonXY([self._points]), None)
            elif event.button() == _RIGHT:
                self._finish_polygon()
            return
        if event.button() != _LEFT:
            return
        if self._fixed_size:
            # A single click places it — no drag needed (see class
            # docstring); handled entirely here rather than on release,
            # since there's nothing left to decide once the size is
            # already fixed.
            pt = QgsPointXY(self.toMapCoordinates(event.pos()))
            geom = self._shape_geom(pt, pt, fixed=self._fixed_size)
            if geom is not None and not geom.isEmpty() and geom.area() > 1e-9:
                self.shape_completed.emit(geom)
            return
        self._anchor = QgsPointXY(self.toMapCoordinates(event.pos()))

    def canvasMoveEvent(self, event):
        pt = QgsPointXY(self.toMapCoordinates(event.pos()))
        if self._mode == 'polygon':
            if len(self._points) >= 2:
                self._band.setToGeometry(
                    QgsGeometry.fromPolygonXY([self._points + [pt]]), None)
            return
        if self._fixed_size:
            # Live preview at the exact size, following the cursor, even
            # before any click — 'input the diameter then click at the
            # point' per explicit request.
            geom = self._shape_geom(pt, pt, fixed=self._fixed_size)
            self._band.setToGeometry(geom if geom is not None else QgsGeometry(), None)
            return
        if self._anchor is None:
            return
        geom = self._shape_geom(self._anchor, pt)
        self._band.setToGeometry(geom if geom is not None else QgsGeometry(), None)

    def canvasReleaseEvent(self, event):
        if (self._mode == 'polygon' or self._fixed_size
                or event.button() != _LEFT or self._anchor is None):
            return
        pt = QgsPointXY(self.toMapCoordinates(event.pos()))
        geom = self._shape_geom(self._anchor, pt)
        self._anchor = None
        self._band.reset(_POLYGON_GEOM)
        if geom is not None and not geom.isEmpty() and geom.area() > 1e-9:
            self.shape_completed.emit(geom)

    def canvasDoubleClickEvent(self, event):
        if self._mode == 'polygon' and event.button() == _LEFT:
            self._finish_polygon()

    def keyPressEvent(self, event):
        """Ctrl+Z undoes the most-recently-added vertex while still
        drawing a polygon — same shortcut, same 'remove one click rather
        than scrap the whole shape' behaviour as ProfileLineTool's own
        (Channel/Embankment Surface's alignment drawing), per explicit
        request. Only meaningful in polygon mode — the other shapes
        commit in a single click/drag, nothing to undo mid-draw."""
        if self._mode == 'polygon':
            is_ctrl = bool(event.modifiers() & _CTRL_MOD)
            if is_ctrl and event.key() == _KEY_Z:
                self._undo_last_polygon_point()
                event.accept()
                return
        super().keyPressEvent(event)

    def _undo_last_polygon_point(self):
        if not self._points:
            return
        self._points.pop()
        self._vertex_band.reset(_POINT_GEOM)
        for p in self._points:
            self._vertex_band.addPoint(p)
        if len(self._points) >= 3:
            self._band.setToGeometry(QgsGeometry.fromPolygonXY([self._points]), None)
        else:
            self._band.reset(_POLYGON_GEOM)
        self._canvas.refresh()

    def _finish_polygon(self):
        pts = self._points
        self.reset()
        if len(pts) >= 3:
            geom = QgsGeometry.fromPolygonXY([pts])
            if not geom.isEmpty() and geom.area() > 1e-9:
                self.shape_completed.emit(geom)

    def deactivate(self):
        self.reset()
        super().deactivate()

    def isEditTool(self):
        return False

    def isTransient(self):
        return False


class ShapeRotateTool(QgsMapTool):
    """Map tool for Shapes and Polygon Surface's Rotate mode — click-drag
    anywhere on the canvas (not necessarily exactly on the shape) to
    rotate the current shape around a fixed centre (set via
    set_center(), normally the shape's own centroid). Purely angle-based
    — only the ANGLE of (centre -> cursor) at press vs. the current
    position matters, not the cursor's distance from the centre, so the
    shape's SIZE never changes, only its orientation. The dock owns the
    actual geometry and does the rotating; this tool only reports the
    angle delta.

    Shows its own pivot marker (a small cross at the rotation centre)
    for as long as this tool stays active — not just while actually
    dragging — plus a distinct cursor, so it's clear at a glance that
    Rotate is engaged and exactly what point it pivots around, per
    explicit request ('hard to know if [rotate] is working or not').

    Signals
    -------
    rotate_preview(float)
        Live angle delta in RADIANS while dragging, standard math
        convention (counter-clockwise-positive, from atan2) — apply to a
        COPY of the shape for the on-screen preview, never commit it.
        NOTE: QgsGeometry.rotate() itself is clockwise-positive, the
        opposite sign — negate this value before passing it there (the
        dock's own handlers already do).
    rotate_committed(float)
        Final angle delta in RADIANS once the drag is released — apply
        and keep. Same sign convention/caveat as rotate_preview.
    """

    rotate_preview   = pyqtSignal(float)
    rotate_committed = pyqtSignal(float)

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self._center = None
        self._press_angle = None
        self.setCursor(QCursor(_CROSS_CURSOR))

        self._pivot_band = QgsRubberBand(canvas, _POINT_GEOM)
        self._pivot_band.setColor(QColor(233, 30, 99, 230))
        self._pivot_band.setIcon(_ICON_CROSS)
        self._pivot_band.setIconSize(16)
        self._pivot_band.setWidth(2)

    def set_center(self, center_pt):
        self._center = QgsPointXY(center_pt) if center_pt is not None else None
        self._pivot_band.reset(_POINT_GEOM)
        if self._center is not None:
            self._pivot_band.addPoint(self._center)

    def _angle_at(self, pt):
        return math.atan2(pt.y() - self._center.y(), pt.x() - self._center.x())

    def canvasPressEvent(self, event):
        if event.button() != _LEFT or self._center is None:
            return
        pt = QgsPointXY(self.toMapCoordinates(event.pos()))
        self._press_angle = self._angle_at(pt)

    def canvasMoveEvent(self, event):
        if self._press_angle is None or self._center is None:
            return
        pt = QgsPointXY(self.toMapCoordinates(event.pos()))
        self.rotate_preview.emit(self._angle_at(pt) - self._press_angle)

    def canvasReleaseEvent(self, event):
        if event.button() != _LEFT or self._press_angle is None or self._center is None:
            return
        pt = QgsPointXY(self.toMapCoordinates(event.pos()))
        delta = self._angle_at(pt) - self._press_angle
        self._press_angle = None
        self.rotate_committed.emit(delta)

    def reset(self):
        self._press_angle = None
        self._center = None
        self._pivot_band.reset(_POINT_GEOM)

    def deactivate(self):
        self.reset()
        super().deactivate()

    def isEditTool(self):
        return False

    def isTransient(self):
        return False
