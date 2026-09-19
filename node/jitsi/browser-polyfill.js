import { DOMImplementation, XMLSerializer } from "@xmldom/xmldom";

/** Minimal browser globals required by lib-jitsi-meet UMD bundle in Node. */

function makeElement(tag, parent) {
  const node = {
    tagName: String(tag || "").toUpperCase(),
    nodeName: String(tag || "").toLowerCase(),
    style: {},
    innerHTML: "",
    firstChild: null,
    children: [],
    id: "",
    className: "",
    width: "0px",
    height: "0px",
    src: "",
    async: false,
    parentNode: parent || null,
    appendChild(child) {
      child.parentNode = this;
      this.children.push(child);
      this.firstChild = this.children[0] || null;
    },
    setAttribute() {},
  };
  return node;
}

const html = makeElement("html", null);
const head = makeElement("head", html);
const body = makeElement("body", html);
const anchorScript = makeElement("script", head);
anchorScript.src = "https://icapture-worker.local/anchor.js";
head.appendChild(anchorScript);
html.appendChild(head);
html.appendChild(body);

globalThis.window = globalThis;
globalThis.self = globalThis;
globalThis.chrome = { webstore: {} };
globalThis.document = {
  readyState: "complete",
  documentElement: html,
  head,
  body,
  createElement: (tag) => makeElement(tag, body),
  createDocumentFragment: () => ({ appendChild() {}, firstChild: null }),
  getElementById: () => null,
  getElementsByTagName: (tag) =>
    String(tag || "").toLowerCase() === "script" ? [anchorScript] : [],
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener() {},
  implementation: {
    createDocument: (ns, qualifiedName, doctype) =>
      new DOMImplementation().createDocument(ns, qualifiedName, doctype),
  },
};
globalThis.XMLSerializer = XMLSerializer;
globalThis.DOMImplementation = DOMImplementation;

const navigatorPolyfill = {
  userAgent:
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0",
  platform: "MacIntel",
  mimeTypes: { length: 0 },
  mediaDevices: {
    enumerateDevices: async () => [],
    getUserMedia: async () => {
      throw new Error("getUserMedia not initialized yet");
    },
    addEventListener() {},
    removeEventListener() {},
  },
};
try {
  globalThis.navigator = navigatorPolyfill;
} catch {
  Object.defineProperty(globalThis, "navigator", {
    value: navigatorPolyfill,
    configurable: true,
    writable: true,
  });
}
globalThis.location = {
  origin: "https://icapture-worker.local",
  href: "https://icapture-worker.local/",
  hostname: "icapture-worker.local",
  host: "icapture-worker.local",
  pathname: "/",
  search: "",
  hash: "",
  protocol: "https:",
};

function makeStorage() {
  const map = new Map();
  return {
    get length() {
      return map.size;
    },
    clear() {
      map.clear();
    },
    getItem(key) {
      return map.has(key) ? map.get(key) : null;
    },
    key(index) {
      return [...map.keys()][index] ?? null;
    },
    removeItem(key) {
      map.delete(key);
    },
    setItem(key, value) {
      map.set(String(key), String(value));
    },
  };
}

globalThis.localStorage = makeStorage();
globalThis.sessionStorage = makeStorage();

globalThis.addEventListener = globalThis.addEventListener || (() => {});
globalThis.removeEventListener = globalThis.removeEventListener || (() => {});
globalThis.attachEvent = (eventName, handler) => {
  const event = String(eventName).replace(/^on/, "");
  globalThis.addEventListener(event, handler);
};
globalThis.detachEvent = (eventName, handler) => {
  const event = String(eventName).replace(/^on/, "");
  globalThis.removeEventListener(event, handler);
};
