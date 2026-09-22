/**
 * Network choice, shared by the create wizard and the "choose the network"
 * dialog on an existing cluster.
 *
 * Extracted rather than written twice: both places show the same candidates
 * with the same evidence, and a divergence between them would mean an
 * operator sees different reasoning depending on which door they came in.
 */
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import Radio from "@mui/material/Radio";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import type { EnvironmentPlan, FabricCandidate } from "~/api/inference";

export interface NetworkPickerProps {
  plan: EnvironmentPlan;
  selectedId: string | null;
  onSelect: (candidateId: string) => void;
}

export function NetworkPicker({ plan, selectedId, onSelect }: NetworkPickerProps) {
  return (
    <Stack spacing={1.5}>
      {plan.blockers.length > 0 && (
        <Alert severity="error">
          <strong>These machines cannot form a cluster yet.</strong>
          <ul style={{ margin: "6px 0 0 16px" }}>
            {plan.blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
        </Alert>
      )}
      {plan.warnings.map((warning) => (
        <Alert key={warning} severity="warning">
          {warning}
        </Alert>
      ))}

      <Typography variant="body2" color="text.secondary">
        These machines can reach each other over the networks below. We recommend
        the fastest one that is not shared with management traffic.
      </Typography>

      {plan.candidates.length === 0 && (
        <Alert severity="info">
          No shared network was found between these machines.
        </Alert>
      )}

      {plan.candidates.map((candidate: FabricCandidate) => (
        <Stack
          key={candidate.candidate_id}
          direction="row"
          spacing={1}
          alignItems="flex-start"
          sx={{
            border: 1,
            borderColor: selectedId === candidate.candidate_id ? "primary.main" : "divider",
            borderRadius: 1,
            px: 1,
            py: 1,
            cursor: "pointer",
          }}
          onClick={() => onSelect(candidate.candidate_id)}
        >
          <Radio
            checked={selectedId === candidate.candidate_id}
            slotProps={{ input: { "aria-label": `Use ${candidate.cidr}` } }}
          />
          <Box sx={{ flexGrow: 1, minWidth: 0 }}>
            <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
              <Typography variant="body2" fontWeight={600}>
                {candidate.speed_gbps} Gb/s {candidate.fabric_type}
              </Typography>
              {candidate.recommended && (
                <Chip size="small" color="success" label="recommended" />
              )}
              {candidate.validation.performed && (
                <Chip
                  size="small"
                  variant="outlined"
                  color={candidate.validation.reachable ? "success" : "error"}
                  label={
                    candidate.validation.reachable
                      ? "we tested it — it works"
                      : "we tested it — unreachable"
                  }
                />
              )}
            </Stack>
            <Typography variant="caption" color="text.secondary" display="block">
              {candidate.cidr}
              {candidate.is_management ? " · shared with management traffic" : ""}
            </Typography>
            {(candidate.recommendation_reason || candidate.reasons.length > 0) && (
              <Typography variant="caption" color="text.secondary" display="block">
                {candidate.recommendation_reason || candidate.reasons.join("; ")}
              </Typography>
            )}
          </Box>
        </Stack>
      ))}
    </Stack>
  );
}
