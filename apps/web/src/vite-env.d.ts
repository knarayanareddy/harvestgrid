/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** `live` calls the API, `fixture` uses the in-browser adapter, `true` builds the judge demo. */
  readonly VITE_DEMO_MODE?: "fixture" | "live" | "true";
  readonly VITE_BASE_PATH?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
