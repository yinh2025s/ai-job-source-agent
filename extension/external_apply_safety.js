(() => {
  const LINKEDIN_HOST = (hostname) => (
    hostname === "linkedin.com" || hostname.endsWith(".linkedin.com")
  );
  const LINKEDIN_OWNED_HOST = (hostname) => (
    LINKEDIN_HOST(hostname) || hostname === "licdn.com"
    || hostname.endsWith(".licdn.com") || hostname === "lnkd.in"
  );
  const LOOKS_LIKE_LINKEDIN_HOST = (hostname) => /(?:linkedin|licdn)/i.test(hostname);
  const SENSITIVE_KEYS = new Set([
    "accesstoken", "apikey", "auth", "authorization", "code", "csrf", "idtoken", "key",
    "lsd", "password", "protectedsessionjwt", "refreshtoken", "secret", "session",
    "sessioncsrftoken", "sessionjwt", "sig", "signature", "state", "token", "xfblsd"
  ]);
  const SENSITIVE_MARKERS = ["token", "secret", "password", "credential", "session", "csrf"];
  const hasSensitiveQuery = (url) => {
    return Array.from(url.searchParams.keys()).some((key) => {
      const canonical = key.toLowerCase().replace(/[^a-z0-9]+/g, "");
      return SENSITIVE_KEYS.has(canonical) || SENSITIVE_MARKERS.some((marker) => canonical.includes(marker));
    });
  };
  const TRACKING_PARAMS = [
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"
  ];
  const isPublicHost = (hostname) => {
    const host = hostname.toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
    if (!host || !host.includes(".") || host === "localhost" || host.endsWith(".localhost")
      || host.endsWith(".local") || host.endsWith(".internal") || host.endsWith(".lan")
      || host.endsWith(".home") || host.endsWith(".private")) return false;
    if (host === "::" || host === "::1" || /^(?:fc|fd|fe8|fe9|fea|feb)/i.test(host)) return false;
    const octets = host.split(".").map(Number);
    if (octets.length !== 4 || octets.some((octet) => !Number.isInteger(octet) || octet < 0 || octet > 255)) {
      return true;
    }
    return !(
      octets[0] === 0 || octets[0] === 10 || octets[0] === 127 || octets[0] >= 224
      || (octets[0] === 100 && octets[1] >= 64 && octets[1] <= 127)
      || (octets[0] === 169 && octets[1] === 254)
      || (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31)
      || (octets[0] === 192 && octets[1] === 0 && (octets[2] === 0 || octets[2] === 2))
      || (octets[0] === 192 && octets[1] === 88 && octets[2] === 99)
      || (octets[0] === 192 && octets[1] === 168)
      || (octets[0] === 198 && (octets[1] === 18 || octets[1] === 19))
      || (octets[0] === 198 && octets[1] === 51 && octets[2] === 100)
      || (octets[0] === 203 && octets[1] === 0 && octets[2] === 113)
    );
  };

  const sanitize = (value, baseUrl = undefined) => {
    try {
      let url = new URL(String(value || "").trim(), baseUrl);
      if (LINKEDIN_HOST(url.hostname.toLowerCase())) {
        if (url.protocol !== "https:" || url.pathname !== "/safety/go/") return "";
        const target = url.searchParams.get("url");
        if (!target) return "";
        url = new URL(target);
      }
      const host = url.hostname.toLowerCase();
      if (url.protocol !== "https:" || url.username || url.password || url.hash) return "";
      if (!isPublicHost(host) || LINKEDIN_OWNED_HOST(host)
        || LOOKS_LIKE_LINKEDIN_HOST(host) || hasSensitiveQuery(url)) return "";
      for (const key of TRACKING_PARAMS) url.searchParams.delete(key);
      return url.href;
    } catch {
      return "";
    }
  };

  globalThis.JobSourceExternalApplySafety = Object.freeze({ sanitize });
})();
