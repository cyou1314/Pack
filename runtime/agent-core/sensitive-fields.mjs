const SENSITIVE_KEYS = new Set([
  "apikey", "xapikey", "accesstoken", "refreshtoken", "oauthtoken", "idtoken", "authtoken", "bearertoken", "token",
  "authorization", "authheader", "password", "cookie", "cookies", "cookiejar", "sessioncookie", "setcookie",
  "nativememory", "privatememory", "privatecontext", "credential", "credentials",
  "clientsecret", "secretkey", "signingkey", "sshprivatekey", "privatekey", "secret",
]);

const SENSITIVE_KEY_MARKERS = [
  "apikey", "accesstoken", "refreshtoken", "oauthtoken", "idtoken",
  "authtoken", "bearertoken", "sessiontoken", "authorization", "authheader",
  "password", "cookie", "credential", "secret", "privatekey",
  "nativememory", "privatememory", "privatecontext",
];

export function findSensitiveField(value, path = "input") {
  if (value === null || typeof value !== "object") return null;
  if (Array.isArray(value)) {
    for (let index = 0; index < value.length; index++) {
      const found = findSensitiveField(value[index], path + "[" + index + "]");
      if (found) return found;
    }
    return null;
  }
  for (const [key, nested] of Object.entries(value)) {
    const normalized = key.normalize("NFKC").toLowerCase().replace(/[^a-z0-9]/g, "");
    if (SENSITIVE_KEYS.has(normalized)
        || SENSITIVE_KEY_MARKERS.some((marker) => normalized.includes(marker))) return {path, key};
    const found = findSensitiveField(nested, path + "." + key);
    if (found) return found;
  }
  return null;
}
