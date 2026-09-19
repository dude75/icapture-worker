import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** Load lib-jitsi-meet UMD bundle in sloppy Node context (implicit globals). */
export function loadJitsiMeetJS() {
  const bundlePath = path.join(
    __dirname,
    "node_modules",
    "lib-jitsi-meet",
    "dist",
    "lib-jitsi-meet.min.js",
  );
  const code = fs.readFileSync(bundlePath, "utf8");
  vm.runInThisContext(code, { filename: "lib-jitsi-meet.min.js" });
  return globalThis.JitsiMeetJS;
}
