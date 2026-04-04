import { useTranslation } from "react-i18next";
import { Link as RouterLink } from "react-router";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Grid from "@mui/material/Grid";
import Typography from "@mui/material/Typography";
import NodePerformanceCard from "./NodePerformanceCard";
import type { ManagedNode } from "~/api/nodes";

interface NodeFleetRowProps {
  nodes: ManagedNode[];
  onRefreshNode: (updated: ManagedNode) => void;
  refreshNode: (nodeId: string) => Promise<ManagedNode>;
}

export default function NodeFleetRow({
  nodes,
  onRefreshNode,
  refreshNode,
}: NodeFleetRowProps) {
  const { t } = useTranslation();

  if (nodes.length === 0) {
    return (
      <Box
        sx={{
          py: 3,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 1,
          border: "1px dashed",
          borderColor: "divider",
          borderRadius: 1,
        }}
      >
        <Typography color="text.secondary">
          {t("dashboard.node_fleet_empty", {
            defaultValue: "No registered nodes",
          })}
        </Typography>
        <Button
          component={RouterLink}
          to="/admin/nodes/onboarding"
          variant="outlined"
          size="small"
        >
          {t("dashboard.node_fleet_onboard", {
            defaultValue: "Onboard a node",
          })}
        </Button>
      </Box>
    );
  }

  // Dynamic column sizing: up to 4 columns on large screens
  const lgSize = 12 / Math.min(nodes.length, 4);

  return (
    <Grid container spacing={1.5}>
      {nodes.map((node) => (
        <Grid key={node.id} size={{ xs: 12, sm: 6, lg: lgSize }}>
          <NodePerformanceCard
            node={node}
            onRefresh={onRefreshNode}
            refreshNode={refreshNode}
          />
        </Grid>
      ))}
    </Grid>
  );
}
