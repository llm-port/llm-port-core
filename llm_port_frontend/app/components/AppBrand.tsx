import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import { useTranslation } from "react-i18next";
import { APP_BRANDING } from "~/branding";

interface AppBrandProps {
  compact?: boolean;
}

export default function AppBrand({ compact = false }: AppBrandProps) {
  const { t } = useTranslation();

  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography
        variant="h6"
        noWrap
        sx={{
          fontSize: compact ? "0.95rem" : "1rem",
          color: "primary.light",
          lineHeight: 1.1,
          fontFamily: APP_BRANDING.titleFontFamily,
          letterSpacing: APP_BRANDING.titleLetterSpacing,
          fontWeight: 700,
        }}
      >
        {APP_BRANDING.title}
      </Typography>
      <Typography
        variant="caption"
        noWrap
        sx={{
          display: "block",
          color: "text.secondary",
          lineHeight: 1.1,
          mt: 0.25,
          fontFamily: APP_BRANDING.subtitleFontFamily,
        }}
      >
        {t("app.subtitle")}
      </Typography>
    </Box>
  );
}

