/// <reference types="vite/client" />
interface ImportMetaEnv {
  readonly VITE_EDGE_URL?: string;
  readonly VITE_CLOUD_URL?: string;
  readonly VITE_STORE_ID?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
