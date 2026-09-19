import { createRequire } from "node:module";
import jQuery from "jquery";
import WebSocket from "ws";

const require = createRequire(import.meta.url);

/** Strophe XMPP stack for lib-jitsi-meet in Node. */
export function installStropheGlobals() {
  globalThis.WebSocket = WebSocket;
  globalThis.$ = globalThis.jQuery = jQuery;

  const strophe = require("strophe.js/dist/strophe.common.js");
  globalThis.Strophe = strophe.Strophe;
  globalThis.$build = strophe.$build;
  globalThis.$iq = strophe.$iq;
  globalThis.$msg = strophe.$msg;
  globalThis.$pres = strophe.$pres;
  globalThis.b64_sha1 = strophe.b64_sha1;

  require("strophejs-plugin-disco");
  require("strophejs-plugin-caps");
}
