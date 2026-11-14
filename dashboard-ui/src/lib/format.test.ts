import { describe, expect, it } from "vitest";
import { fmtFact, fmtNumber, unitOfKey } from "./format";

describe("number formatting", () => {
  it("rounds long numbers to 4 significant figures and keeps integers", () => {
    expect(fmtNumber(191.872)).toBe("191.9");
    expect(fmtNumber(7.45547)).toBe("7.455");
    expect(fmtNumber(0.010204)).toBe("0.0102");
    expect(fmtNumber(400)).toBe("400");
    expect(fmtNumber(0.2)).toBe("0.2");
  });
  it("takes units from the fact name, including on a dotted parent", () => {
    expect(unitOfKey("dry_mass_g")).toBe("g");
    expect(unitOfKey("mode_power_mw.sleep")).toBe("mW");
    expect(unitOfKey("wind_speed_ms")).toBe("m/s");
    expect(unitOfKey("air_temp_c")).toBe("°C");
    expect(unitOfKey("proposer")).toBe("");
  });
  it("writes a fact with its unit, arrays as dimensions, and text without one", () => {
    expect(fmtFact("battery_wh", 0.666)).toBe("0.666 Wh");
    expect(fmtFact("bbox_mm", [114, 114, 43.2])).toBe("114 × 114 × 43.2 mm");
    expect(fmtFact("proposer_g", "deterministic")).toBe("deterministic");
    expect(fmtFact("erc_errors", 0)).toBe("0");
  });
});
