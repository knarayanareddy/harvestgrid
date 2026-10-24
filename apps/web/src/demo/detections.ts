/**
 * Camera-frame detections for the demo, kept separate from the privacy geometry.
 *
 * A "frame" is a small, deterministic set of fruit clusters in normalized coordinates standing
 * in for a Ring snapshot. Nothing here decodes media: the demo never receives a URL, a clip, or
 * an image. Detections are the only thing the mock webhook carries, which is also true of the
 * real pipeline after feature extraction.
 *
 * Boxes are scored, then filtered: a cluster survives only if at least half of its box falls
 * inside allowed (ROI-minus-mask) cells. That is the same rule the Python detector applies
 * before a detection can become a feature row.
 */

import { allowedFraction, type AllowedRaster, type Box } from "./geometry";

export interface FruitCluster extends Box {
  readonly clusterId: string;
  readonly confidence: number;
  readonly ripeness: "unripe" | "turning" | "ripe" | "overripe";
}

export interface FilteredFrame {
  readonly kept: readonly FruitCluster[];
  readonly dropped: readonly FruitCluster[];
  readonly droppedByMask: number;
  readonly droppedByRoi: number;
}

export function syntheticClusters(seed = 7): readonly FruitCluster[] {
  // Deterministic pseudo-random placement: a small LCG, so a judge sees the same frame twice.
  let state = seed;
  const next = (): number => {
    state = (state * 1103515245 + 12345) % 2147483648;
    return state / 2147483648;
  };
  const clusters: FruitCluster[] = [];
  const ripeness: readonly FruitCluster["ripeness"][] = ["unripe", "turning", "ripe", "overripe"];
  for (let index = 0; index < 16; index += 1) {
    const width = 0.05 + next() * 0.06;
    const height = width * (0.85 + next() * 0.3);
    clusters.push({
      clusterId: `cluster-${String(index + 1).padStart(2, "0")}`,
      centerX: 0.06 + next() * 0.88,
      centerY: 0.08 + next() * 0.84,
      width,
      height,
      confidence: Math.round((0.42 + next() * 0.55) * 1000) / 1000,
      ripeness: ripeness[Math.floor(next() * ripeness.length)] ?? "turning",
    });
  }
  return clusters;
}

export function filterClusters(
  clusters: readonly FruitCluster[],
  raster: AllowedRaster,
): FilteredFrame {
  const kept: FruitCluster[] = [];
  const dropped: FruitCluster[] = [];
  for (const cluster of clusters) {
    if (allowedFraction(cluster, raster) >= 0.5) kept.push(cluster);
    else dropped.push(cluster);
  }
  return {
    kept: kept.sort((first, second) => first.centerX - second.centerX || first.centerY - second.centerY),
    dropped,
    droppedByMask: dropped.filter((cluster) => allowedFraction(cluster, raster) === 0).length,
    droppedByRoi: dropped.filter((cluster) => {
      const fraction = allowedFraction(cluster, raster);
      return fraction > 0 && fraction < 0.5;
    }).length,
  };
}

/** Share of visible clusters judged ripe or overripe; a visible-colour statement only. */
export function ripeShare(clusters: readonly FruitCluster[]): number {
  if (clusters.length === 0) return 0;
  const ripe = clusters.filter(
    (cluster) => cluster.ripeness === "ripe" || cluster.ripeness === "overripe",
  ).length;
  return ripe / clusters.length;
}

export const RIPENESS_LABELS: Readonly<Record<FruitCluster["ripeness"], string>> = {
  unripe: "Unripe",
  turning: "Turning",
  ripe: "Ripe",
  overripe: "Overripe",
};

export const RIPENESS_COLORS: Readonly<Record<FruitCluster["ripeness"], string>> = {
  unripe: "#6E8B3D",
  turning: "#B98A2E",
  ripe: "#9C3B57",
  overripe: "#5C2A38",
};
