import type { Route } from "./+types/home";
import { Link as RouterLink } from "react-router";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import DnsIcon from "@mui/icons-material/Dns";
import { useTranslation } from "react-i18next";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "AIrgap Console" },
    { name: "description", content: "Airgap container management console" },
  ];
}

export default function Home() {
  const { t } = useTranslation();
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
          {t("app.title")}
        </Typography>
        <Typography
          variant="body1"
          color="text.secondary"
          textAlign="center"
          maxWidth={420}
        >
          {t("home.subtitle")}
        </Typography>
        <Button
          component={RouterLink}
          to="/login"
          variant="contained"
          size="large"
          sx={{ px: 4, py: 1.2 }}
        >
          {t("auth.sign_in")}
        </Button>
      </Stack>
    </Box>
  );
}
