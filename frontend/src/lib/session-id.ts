interface SessionCrypto {
  randomUUID?: () => string;
  getRandomValues: (values: Uint8Array) => Uint8Array;
}

/** Create an opaque import-session identifier without requiring randomUUID support. */
export function createImportSessionId(cryptoApi: SessionCrypto = crypto): string {
  if (typeof cryptoApi.randomUUID === "function") {
    return `session_${cryptoApi.randomUUID().replaceAll("-", "").slice(0, 12)}`;
  }

  const bytes = cryptoApi.getRandomValues(new Uint8Array(6));
  const suffix = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  return `session_${suffix}`;
}
