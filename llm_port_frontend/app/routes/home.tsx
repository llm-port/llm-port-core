import type { Route } from "./+types/home";
import { Link as RouterLink } from "react-router";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import { useTranslation } from "react-i18next";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "llm-port" },
    { name: "description", content: "llm-port container management console" },
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
          component="img"
          src="/icon_color.png"
          alt="llm-port"
          sx={{ width: 96, height: 96, objectFit: "contain" }}
        />
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
