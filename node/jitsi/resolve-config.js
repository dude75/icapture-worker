/** Fetch and parse Jitsi Meet `config.js` for XMPP host settings. */

const CONFIG_CACHE = new Map();
const CONFIG_TTL_MS = 5 * 60 * 1000;

function parseConfigField(text, field) {
  const re = new RegExp(`config\\.${field}\\s*=\\s*['"]([^'"]+)['"]`);
  return text.match(re)?.[1] || null;
}

function parseSubdomainPrefix(text) {
  const subdomain = text.match(/var subdomain = '([^']*)'/)?.[1] || "";
  if (!subdomain) {
    return "";
  }
  return `${subdomain.substring(0, subdomain.length - 1).split(".").join("_").toLowerCase()}.`;
}

function parseSubdir(text) {
  return text.match(/var subdir = '([^']*)'/)?.[1] || "";
}

function parseMuc(text) {
  const dynamic = text.match(
    /config\.hosts\.muc\s*=\s*'muc\.'\s*\+\s*subdomain\s*\+\s*'([^']+)'/,
  );
  if (dynamic) {
    return `muc.${parseSubdomainPrefix(text)}${dynamic[1]}`;
  }

  const literal = text.match(/config\.hosts\.muc\s*=\s*['"]([^'"]+)['"]/);
  if (literal) {
    return literal[1];
  }

  return null;
}

function parseWebsocket(text, meetingHost) {
  const dynamic = text.match(
    /config\.websocket\s*=\s*['"]([^'"]+)['"]\s*\+\s*subdir\s*\+\s*['"]([^'"]+)['"]/,
  );
  if (dynamic) {
    return `${dynamic[1]}${parseSubdir(text)}${dynamic[2]}`;
  }

  const literal = parseConfigField(text, "websocket");
  if (literal) {
    return literal;
  }

  return `wss://${meetingHost}/xmpp-websocket`;
}

export async function resolveJitsiConfig(meetingHost) {
  const cached = CONFIG_CACHE.get(meetingHost);
  if (cached && Date.now() - cached.fetchedAt < CONFIG_TTL_MS) {
    return cached.config;
  }

  const url = `https://${meetingHost}/config.js`;
  const response = await fetch(url, { signal: AbortSignal.timeout(10_000) });
  if (!response.ok) {
    throw new Error(`failed to fetch ${url}: HTTP ${response.status}`);
  }

  const text = await response.text();
  const domain = parseConfigField(text, "hosts.domain") || meetingHost;
  const muc = parseMuc(text) || `conference.${domain}`;
  const anonymousdomain = parseConfigField(text, "hosts.anonymousdomain");
  const authdomain = parseConfigField(text, "hosts.authdomain") || domain;
  const websocket = parseWebsocket(text, meetingHost);

  const config = {
    domain,
    muc,
    anonymousdomain,
    authdomain,
    websocket,
  };
  CONFIG_CACHE.set(meetingHost, { fetchedAt: Date.now(), config });
  return config;
}
