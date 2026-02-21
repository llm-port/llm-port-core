import type { Route } from "./+types/home";
import { Link as RouterLink } from "react-router";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import DnsIcon from "@mui/icons-material/Dns";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "AIrgap Console" },
    { name: "description", content: "Airgap container management console" },
  ];
}

export default function Home() {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "100vh",
        bgcolor: "background.default",
      }}
    >
      <Stack alignItems="center" spacing={3} sx={{ p: 4 }}>
        <Box
          sx={{
            width: 72,
            height: 72,
            borderRadius: "50%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "linear-gradient(135deg, #7c4dff 0%, #00e5ff 100%)",
          }}
        >
          <DnsIcon sx={{ fontSize: 36, color: "#fff" }} />
        </Box>
        <Typography variant="h4" color="text.primary">
          AIrgap Console
        </Typography>
        <Typography
          variant="body1"
          color="text.secondary"
          textAlign="center"
          maxWidth={420}
        >
          Container management, stack deployments, and audit logging for
          air-gapped environments.
        </Typography>
        <Button
          component={RouterLink}
          to="/admin/containers"
          variant="contained"
          size="large"
          sx={{ px: 4, py: 1.2 }}
        >
          Open Admin Dashboard
        </Button>
      </Stack>
    </Box>
  );
}
