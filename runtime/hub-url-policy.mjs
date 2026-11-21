/// Shared policy for every Pack client that sends Hub credentials.
export function validateHubURL(value) {
  if (typeof value !== "string" || !value || value !== value.trim()) {
    throw new TypeError("invalid_hub_url");
  }

  let url;
  try {
    url = new URL(value);
  } catch {
    throw new TypeError("invalid_hub_url");
  }

  if (!["https:", "http:"].includes(url.protocol)
      || !url.hostname
      || url.username
      || url.password
      || url.search
      || url.hash
      || url.href.includes("?")
      || url.href.includes("#")) {
    throw new TypeError("invalid_hub_url");
  }

  if (url.protocol === "http:") {
    let host = url.hostname.toLowerCase();
    if (host.startsWith("[") && host.endsWith("]")) host = host.slice(1, -1);
    if (host.endsWith(".")) host = host.slice(0, -1);
    if (!["127.0.0.1", "localhost", "::1"].includes(host)) {
      throw new TypeError("invalid_hub_url");
    }
  }

  return url.href;
}
