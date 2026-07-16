(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LumeriWorkspaceLayout = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const COLUMNS = 12;
  const DEFAULT_SIZES = Object.freeze({
    preview: Object.freeze({ cols: 8, rows: 6 }),
    timeline: Object.freeze({ cols: 12, rows: 4 }),
    outline: Object.freeze({ cols: 4, rows: 3 }),
    tasks: Object.freeze({ cols: 4, rows: 3 }),
    files: Object.freeze({ cols: 4, rows: 3 }),
    history: Object.freeze({ cols: 4, rows: 3 }),
  });
  const LIMITS = Object.freeze({
    preview: Object.freeze({ minCols: 4, maxCols: 12, minRows: 3, maxRows: 10 }),
    timeline: Object.freeze({ minCols: 6, maxCols: 12, minRows: 2, maxRows: 8 }),
    panel: Object.freeze({ minCols: 3, maxCols: 8, minRows: 2, maxRows: 8 }),
  });

  const int = (value, fallback) => Number.isFinite(Number(value)) ? Math.round(Number(value)) : fallback;
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

  function limitsFor(id) {
    if (id === "preview") return LIMITS.preview;
    if (id === "timeline") return LIMITS.timeline;
    return LIMITS.panel;
  }

  function clampSize(id, size) {
    const fallback = DEFAULT_SIZES[id] || DEFAULT_SIZES.outline;
    const limits = limitsFor(id);
    return {
      cols: clamp(int(size?.cols, fallback.cols), limits.minCols, limits.maxCols),
      rows: clamp(int(size?.rows, fallback.rows), limits.minRows, limits.maxRows),
    };
  }

  // Deterministic row-major first-fit packing on a 12-column occupancy matrix.
  // A rectangle is committed only after every requested cell is free, which is
  // the non-overlap invariant used by both initial layout and every drag/resize.
  function packModules(items, columns = COLUMNS) {
    const width = clamp(int(columns, COLUMNS), 1, COLUMNS);
    const occupied = [];
    const placements = {};
    let usedRows = 0;

    const cellTaken = (row, col) => Boolean(occupied[row]?.[col]);
    const canPlace = (row, col, cols, rows) => {
      if (col + cols > width) return false;
      for (let y = row; y < row + rows; y += 1) {
        for (let x = col; x < col + cols; x += 1) {
          if (cellTaken(y, x)) return false;
        }
      }
      return true;
    };
    const occupy = (id, row, col, cols, rows) => {
      for (let y = row; y < row + rows; y += 1) {
        if (!occupied[y]) occupied[y] = Array(width).fill("");
        for (let x = col; x < col + cols; x += 1) occupied[y][x] = id;
      }
    };

    const seen = new Set();
    for (const raw of Array.isArray(items) ? items : []) {
      const id = String(raw?.id || "").trim();
      if (!id || seen.has(id)) continue;
      seen.add(id);
      const size = clampSize(id, raw);
      const cols = Math.min(size.cols, width);
      const rows = size.rows;
      let placed = false;
      for (let row = 0; !placed; row += 1) {
        for (let col = 0; col <= width - cols; col += 1) {
          if (!canPlace(row, col, cols, rows)) continue;
          occupy(id, row, col, cols, rows);
          placements[id] = { col: col + 1, row: row + 1, cols, rows };
          usedRows = Math.max(usedRows, row + rows);
          placed = true;
          break;
        }
      }
    }
    return { columns: width, rows: Math.max(1, usedRows), placements };
  }

  function hasOverlap(placements) {
    const cells = new Set();
    for (const place of Object.values(placements || {})) {
      for (let row = place.row; row < place.row + place.rows; row += 1) {
        for (let col = place.col; col < place.col + place.cols; col += 1) {
          const key = `${row}:${col}`;
          if (cells.has(key)) return true;
          cells.add(key);
        }
      }
    }
    return false;
  }

  return { COLUMNS, DEFAULT_SIZES, LIMITS, clampSize, packModules, hasOverlap };
});
