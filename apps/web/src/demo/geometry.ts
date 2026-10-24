/**
 * ROI / exclusion-mask geometry for the demo.
 *
 * Mirrors the rules in `packages/domain/harvestgrid_domain/roi.py`: the allowed region is the
 * ROI polygon **minus** every exclusion mask, cell tests use cell centres, and a detection is
 * kept only when at least half of its area falls inside allowed cells. Numbers here are for the
 * interactive demo; the Python implementation remains authoritative for stored views.
 */

export interface Point {
  readonly x: number;
  readonly y: number;
}

export type MaskKind = "neighbor_property" | "public_sidewalk" | "street" | "other_unrelated";

export const MASK_LABELS: Readonly<Record<MaskKind, string>> = {
  neighbor_property: "Neighbour property",
  public_sidewalk: "Public sidewalk",
  street: "Street",
  other_unrelated: "Other unrelated area",
};

export const MIN_ROI_FRAME_FRACTION = 0.01;
export const MIN_EFFECTIVE_ROI_FRACTION = 0.25;
export const MAX_MASK_COVERAGE = 0.9;
export const MIN_ALLOWED_BOX_FRACTION = 0.5;
export const GEOMETRY_SAMPLES = 96;

export interface MaskRegion {
  readonly id: string;
  readonly kind: MaskKind;
  /** Axis-aligned rectangle in normalized frame coordinates. */
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export function polygonArea(points: readonly Point[]): number {
  let total = 0;
  for (let index = 0; index < points.length; index += 1) {
    const current = points[index];
    const next = points[(index + 1) % points.length];
    if (!current || !next) continue;
    total += current.x * next.y - next.x * current.y;
  }
  return Math.abs(total) / 2;
}

export function pointInPolygon(point: Point, polygon: readonly Point[]): boolean {
  let inside = false;
  for (let index = 0; index < polygon.length; index += 1) {
    const first = polygon[index];
    const second = polygon[(index + 1) % polygon.length];
    if (!first || !second) continue;
    if ((first.y > point.y) !== (second.y > point.y)) {
      const intersectionX =
        first.x + ((point.y - first.y) * (second.x - first.x)) / (second.y - first.y);
      if (point.x < intersectionX) inside = !inside;
    }
  }
  return inside;
}

function inMask(point: Point, mask: MaskRegion): boolean {
  return (
    point.x >= mask.x &&
    point.x <= mask.x + mask.width &&
    point.y >= mask.y &&
    point.y <= mask.y + mask.height
  );
}

export function maskCoverageOfRoi(
  roi: readonly Point[],
  masks: readonly MaskRegion[],
  samples: number = GEOMETRY_SAMPLES,
): number {
  if (masks.length === 0) return 0;
  let roiCells = 0;
  let maskedCells = 0;
  for (let row = 0; row < samples; row += 1) {
    for (let column = 0; column < samples; column += 1) {
      const point = { x: (column + 0.5) / samples, y: (row + 0.5) / samples };
      if (!pointInPolygon(point, roi)) continue;
      roiCells += 1;
      if (masks.some((mask) => inMask(point, mask))) maskedCells += 1;
    }
  }
  if (roiCells === 0) return 0;
  return maskedCells / roiCells;
}

export interface AllowedRaster {
  readonly columns: number;
  readonly rows: number;
  readonly cells: readonly (readonly boolean[])[];
}

export function allowedRaster(
  roi: readonly Point[],
  masks: readonly MaskRegion[],
  columns = 32,
  rows = 24,
): AllowedRaster {
  const cells: boolean[][] = [];
  for (let row = 0; row < rows; row += 1) {
    const line: boolean[] = [];
    for (let column = 0; column < columns; column += 1) {
      const point = { x: (column + 0.5) / columns, y: (row + 0.5) / rows };
      const inside = pointInPolygon(point, roi);
      line.push(inside && !masks.some((mask) => inMask(point, mask)));
    }
    cells.push(line);
  }
  return { columns, rows, cells };
}

export interface Box {
  readonly centerX: number;
  readonly centerY: number;
  readonly width: number;
  readonly height: number;
}

export function allowedFraction(box: Box, raster: AllowedRaster): number {
  const left = Math.max(0, Math.floor((box.centerX - box.width / 2) * raster.columns));
  const right = Math.min(
    raster.columns,
    Math.ceil((box.centerX + box.width / 2) * raster.columns),
  );
  const top = Math.max(0, Math.floor((box.centerY - box.height / 2) * raster.rows));
  const bottom = Math.min(raster.rows, Math.ceil((box.centerY + box.height / 2) * raster.rows));
  let total = 0;
  let inside = 0;
  for (let row = top; row < bottom; row += 1) {
    const line = raster.cells[row];
    if (!line) continue;
    for (let column = left; column < right; column += 1) {
      total += 1;
      if (line[column] === true) inside += 1;
    }
  }
  return total === 0 ? 0 : inside / total;
}

export interface ViewValidation {
  readonly ok: boolean;
  readonly roiArea: number;
  readonly maskCoverage: number;
  readonly effectiveRoi: number;
  readonly allowedFraction: number;
  readonly reason: string | null;
  readonly message: string;
}

/**
 * Apply the stored-view acceptance rules a coordinator must satisfy before collection can run.
 * Order matters: the first failure is reported, exactly like the Python validator.
 */
export function validateView(
  roi: readonly Point[],
  masks: readonly MaskRegion[],
): ViewValidation {
  const area = polygonArea(roi);
  const coverage = maskCoverageOfRoi(roi, masks);
  const effective = Math.max(0, 1 - coverage);
  const raster = allowedRaster(roi, masks, 24, 18);
  let allowed = 0;
  for (const line of raster.cells) {
    for (const cell of line) if (cell) allowed += 1;
  }
  const base = {
    roiArea: area,
    maskCoverage: coverage,
    effectiveRoi: effective,
    allowedFraction: allowed / (raster.columns * raster.rows),
  };
  if (area < MIN_ROI_FRAME_FRACTION) {
    return {
      ...base,
      ok: false,
      reason: "roi_area_too_small",
      message: "The tree region is too small to collect from. Draw a larger polygon.",
    };
  }
  if (coverage > MAX_MASK_COVERAGE || effective < MIN_EFFECTIVE_ROI_FRACTION) {
    return {
      ...base,
      ok: false,
      reason: "roi_obscured_by_masks",
      message: `Privacy masks cover ${Math.round(coverage * 100)}% of the region; at most ${Math.round(
        MAX_MASK_COVERAGE * 100,
      )}% may be hidden and at least ${Math.round(MIN_EFFECTIVE_ROI_FRACTION * 100)}% must remain.`,
    };
  }
  return {
    ...base,
    ok: true,
    reason: null,
    message: "View satisfies the ROI and privacy-mask rules. It still needs owner and coordinator approval.",
  };
}

/** Move one polygon vertex, clamped to the frame. */
export function withMovedPoint(
  points: readonly Point[],
  index: number,
  next: Point,
): readonly Point[] {
  return points.map((point, position) =>
    position === index
      ? { x: Math.min(1, Math.max(0, next.x)), y: Math.min(1, Math.max(0, next.y)) }
      : point,
  );
}
