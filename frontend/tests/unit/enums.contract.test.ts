import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { ENUMS } from "../../src/domain/enums";

const here = dirname(fileURLToPath(import.meta.url));
const contractPath = resolve(here, "../../../contracts/domain_enums.json");

interface DomainContract {
  contract: string;
  version: string;
  enums: Record<string, string[]>;
}

function loadContract(): DomainContract {
  expect(
    existsSync(contractPath),
    `${contractPath} is missing; run scripts/generate_domain_contracts.py explicitly`,
  ).toBe(true);
  return JSON.parse(readFileSync(contractPath, "utf-8")) as DomainContract;
}

describe("domain enum contract (read-only consistency check)", () => {
  it("declares the expected contract schema", () => {
    const contract = loadContract();
    expect(contract.contract).toBe("domain_enums");
    expect(contract.version.length).toBeGreaterThan(0);
  });

  it("TypeScript enums match the frozen contract exactly", () => {
    const contract = loadContract();
    expect(Object.keys(contract.enums).sort()).toEqual(
      Object.keys(ENUMS).sort(),
    );
    for (const [name, expected] of Object.entries(contract.enums)) {
      const enumObject = ENUMS[name as keyof typeof ENUMS] as unknown as Record<
        string,
        string
      >;
      const actual = Object.values(enumObject);
      expect(actual, `enum ${name} drifted from the frozen contract`).toEqual(
        expected,
      );
    }
  });
});
