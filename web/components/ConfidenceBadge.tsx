// How sure vision is of a screen read (shared/types.ts ReadConfidence): high / medium / low,
// from its vote. Shape and color both change, so it reads in a video and without color.
// null or missing = the hardware has no vote (FakeHardware, an LED's pixel check): no badge,
// we don't make one up.

import type { ReadConfidence } from "@contract/types";

const DOTS: Record<ReadConfidence, string> = {
  high: "●●●",
  medium: "●●○",
  low: "●○○",
};

interface Props {
  confidence: ReadConfidence | null | undefined;
  size?: "sm" | "lg";
}

export default function ConfidenceBadge({ confidence, size = "sm" }: Props) {
  if (confidence !== "high" && confidence !== "medium" && confidence !== "low") return null;
  return (
    <span
      className={`conf conf-${confidence} conf-${size}`}
      title={`Vision is ${confidence === "low" ? "not sure of" : confidence === "medium" ? "fairly sure of" : "sure of"} this read`}
    >
      <span className="conf-dots" aria-hidden>
        {DOTS[confidence]}
      </span>
      {confidence}
    </span>
  );
}
