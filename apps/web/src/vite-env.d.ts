/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DEMO_MODE?: "fixture" | "live";
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
