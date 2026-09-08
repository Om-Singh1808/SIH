declare module "node:path" {
  const path: {
    dirname(value: string): string;
    resolve(...parts: string[]): string;
  };
  export default path;
}

declare module "node:url" {
  export function fileURLToPath(value: string | URL): string;
}
