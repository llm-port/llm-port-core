/**
 * A compact row of stat cards — the product's one way of showing "a handful
 * of numbers about a thing that is running".
 *
 * Extracted because two screens needed it and were drifting apart. The
 * providers table's row expander had these cards; the deployment page showed
 * the same kind of information as label/value pairs, so the two pages looked
 * like different products describing the same cluster. Now both render this.
 *
 * Purely presentational: it takes values and renders them. Where the numbers
 * come from — a provider's Prometheus series, a deployment's gateway request
 * log — is the caller's business, and keeping that out is what lets one
 * component serve both.
 *
 * The rules it enforces are the ones the rest of the product follows:
 *
 * - **A gap is never a zero.** A card with no value says so and is dimmed;
 *   it does not render `0`, because an absent series and an idle engine are
 *   different facts and an operator acts on them differently.
 * - **Loading is not absence.** While the first fetch is in flight a card
 *   shows a skeleton, not "No data" — otherwise every page flashes a wall of
 *   "No data" on its way to the numbers.
 */
import type { ReactNode } from "react";

import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

export interface StatCardItem {
  /** Stable identity for the list. */
  key: string;
  label: string;
  /**
   * The formatted value, or `null` when there is nothing to show.
   *
   * Formatting belongs to the caller: percentages, token rates and
   * millisecond figures all round differently, and a component that guessed
   * would be wrong somewhere.
   */
  value: string | null;
  /** Why there is no value, shown on hover. */
  emptyHint?: string;
}

export interface StatCardRowProps {
  cards: StatCardItem[];
  /** True while the first values are still on their way. */
  loading?: boolean;
  /** Shown above the cards — a title, and any actions. */
  header?: ReactNode;
  /** Text for a card with no value. */
  emptyLabel?: string;
  /** Wraps the whole row; used where the row itself is clickable. */
  sx?: object;
}

export function StatCardRow({
  cards,
  loading = false,
  header,
  emptyLabel = "No data",
  sx,
}: StatCardRowProps) {
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 0.75,
        px: 1,
        py: 0.5,
        mt: 0.5,
        borderRadius: "8px",
        bgcolor: "action.hover",
        border: "1px solid",
        borderColor: "divider",
        ...sx,
      }}
    >
      {header}
      <Stack direction="row" flexWrap="wrap" spacing={0.5}>
        {cards.map((card) => {
          const hasValue = card.value !== null;
          // A skeleton only before anything has arrived. Once a fetch has
          // answered, "no value" is the answer and should be shown as one.
          const pending = loading && !hasValue;
          return (
            <Tooltip
              key={card.key}
              title={hasValue || pending ? undefined : (card.emptyHint ?? "")}
              enterDelay={500}
            >
              <Card
                variant="outlined"
                sx={{
                  minWidth: 96,
                  flex: "1 1 96px",
                  borderRadius: "6px",
                  opacity: hasValue ? 1 : 0.6,
                  "&:hover": {
                    bgcolor: "action.hover",
                    borderColor: "primary.main",
                  },
                }}
              >
                <Box sx={{ px: 1, py: 0.75 }}>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: "block", mb: 0.25 }}
                  >
                    {card.label}
                  </Typography>
                  {pending ? (
                    <Skeleton width={56} height={22} />
                  ) : (
                    <Typography
                      variant="subtitle1"
                      sx={{
                        fontWeight: 700,
                        fontSize: "1.05rem",
                        color: hasValue ? "text.primary" : "text.disabled",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {hasValue ? card.value : emptyLabel}
                    </Typography>
                  )}
                </Box>
              </Card>
            </Tooltip>
          );
        })}
      </Stack>
    </Box>
  );
}

export default StatCardRow;
