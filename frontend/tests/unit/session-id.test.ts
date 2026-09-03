import { describe, expect, it } from "vitest";

import { createImportSessionId } from "../../src/lib/session-id";

describe("createImportSessionId", () => {
  it("uses randomUUID when the browser provides it", () => {
    const result = createImportSessionId({
      randomUUID: () => "12345678-90ab-cdef-1234-567890abcdef",
      getRandomValues: (values) => values,
    });

    expect(result).toBe("session_1234567890ab");
  });

  it("uses getRandomValues when randomUUID is unavailable", () => {
    const result = createImportSessionId({
      getRandomValues: (values) => {
        values.set([0, 1, 15, 16, 254, 255]);
        return values;
      },
    });

    expect(result).toBe("session_00010f10feff");
  });
});
