/**
 * React binding for {@link DemoStore}.
 *
 * `useSyncExternalStore` keeps rendering consistent with the store without copying state into
 * component state; the store is external and synchronous, so this stays a two-line hook.
 */

import { useSyncExternalStore } from "react";

import { DemoStore, type DemoState } from "./demoStore";

export function useDemoStore(store: DemoStore): DemoState {
  return useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
}
