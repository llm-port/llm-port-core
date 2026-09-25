import Box from "@mui/material/Box";
import Tooltip from "@mui/material/Tooltip";

export interface ResidencyBarProps {
  insidePct: number;
  unknownPct: number;
  externalPct: number;
  height?: number;
  /** Tooltip per segment; a segment without one has none. */
  labels?: { inside?: string; unknown?: string; external?: string };
}

/**
 * Inside, unknown and external shares of the providers in one bar. Unknown is
 * drawn apart: it is neither known to stay nor known to leave.
 */
export default function ResidencyBar({ insidePct, unknownPct, externalPct, height = 12, labels = {} }: ResidencyBarProps) {
  const segments = [
    { key: "inside", pct: insidePct, color: "success.main", label: labels.inside },
    { key: "unknown", pct: unknownPct, color: "text.disabled", label: labels.unknown },
    { key: "external", pct: externalPct, color: "warning.light", label: labels.external },
  ].filter((s) => s.pct > 0);

  return (
    <Box
      role="img"
      aria-label={[labels.inside, labels.unknown, labels.external].filter(Boolean).join(", ")}
      sx={{ display: "flex", height, borderRadius: 1.5, overflow: "hidden", bgcolor: "action.hover" }}
    >
      {segments.map((s) => (
        <Tooltip key={s.key} title={s.label ?? ""} disableHoverListener={!s.label}>
          <Box data-testid={`residency-bar-${s.key}`} sx={{ width: `${s.pct}%`, bgcolor: s.color }} />
        </Tooltip>
      ))}
    </Box>
  );
}
