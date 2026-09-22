/**
 * One section's own loading state, so a slow call cannot hold the page hostage.
 *
 * The pattern this replaces: a page gathers everything in one `Promise.all`,
 * renders a single spinner until the slowest call returns, and shows an error
 * only once all of them have settled. That is at its worst exactly when it
 * matters most — a cluster whose nodes are unwell is the case where one call
 * hangs, and it is also the case where the operator most needs the page.
 *
 * So each section loads independently and says so independently. The frame,
 * the title and the navigation are on screen immediately; the parts fill in
 * as they arrive; a part that fails says so in its own place and leaves the
 * rest of the page working.
 *
 * This is the loading-time form of the rule the rest of the product already
 * follows: never render a gap as though it were a value. A skeleton means
 * "not yet", an inline error means "not this one", and neither is a zero.
 */
import type { ReactNode } from "react";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";

export interface AsyncSectionProps {
  /** True while this section's own fetcher is running. */
  loading: boolean;
  /** This section's own error, if its last load failed. */
  error?: string | null;
  /**
   * True when there is nothing to show yet.
   *
   * Kept separate from `loading` so a *refresh* of data already on screen
   * does not blank it out — the operator keeps reading the previous values
   * while the new ones are on their way.
   */
  empty?: boolean;
  /** How tall the placeholder should be, so the layout does not jump. */
  height?: number;
  /** A placeholder shaped like the real content, when a bar is too crude. */
  skeleton?: ReactNode;
  /** Shown instead of the error when this section is optional. */
  fallback?: ReactNode;
  children: ReactNode;
}

/**
 * Render `children`, a skeleton, or this section's own error.
 *
 * Precedence is deliberate: an error wins over a skeleton, because a section
 * that has failed is not still loading; and content wins over both, because
 * stale-but-real beats a placeholder.
 */
export function AsyncSection({
  loading,
  error = null,
  empty = false,
  height = 96,
  skeleton,
  fallback,
  children,
}: AsyncSectionProps) {
  const hasContent = !empty;

  if (error && !hasContent) {
    if (fallback !== undefined) return <>{fallback}</>;
    return (
      <Alert severity="warning" variant="outlined">
        {error}
      </Alert>
    );
  }

  if (loading && !hasContent) {
    return (
      <>
        {skeleton ?? (
          <Skeleton variant="rounded" height={height} animation="wave" />
        )}
      </>
    );
  }

  return (
    <>
      {/* A failure during a refresh: keep the last good content, say the
          update did not land rather than silently showing stale values. */}
      {error && (
        <Alert severity="warning" variant="outlined" sx={{ mb: 1 }}>
          {error}
        </Alert>
      )}
      {children}
    </>
  );
}

/** A placeholder shaped like a row of summary cards. */
export function CardRowSkeleton({ count = 4 }: { count?: number }) {
  return (
    <Stack direction="row" spacing={2}>
      {Array.from({ length: count }, (_, index) => (
        <Box key={index} sx={{ flex: 1 }}>
          <Skeleton variant="rounded" height={88} animation="wave" />
        </Box>
      ))}
    </Stack>
  );
}

/** A placeholder shaped like a small table. */
export function TableSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <Stack spacing={1}>
      <Skeleton variant="text" width="40%" animation="wave" />
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} variant="rounded" height={32} animation="wave" />
      ))}
    </Stack>
  );
}
