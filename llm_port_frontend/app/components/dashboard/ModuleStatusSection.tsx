import { useTranslation } from "react-i18next";
import { useServices } from "~/lib/ServicesContext";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

function moduleStatusColor(
  status: string,
): "success" | "warning" | "error" | "default" {
  if (status === "healthy") return "success";
  if (status === "configured") return "warning";
  if (status === "unhealthy") return "error";
  return "default";
}

export default function ModuleStatusSection() {
  const { t } = useTranslation();
  const { services, loading: servicesLoading } = useServices();

  if (servicesLoading || services.length === 0) return null;

  return (
    <Card variant="outlined">
      <CardContent>
        <Typography variant="h6" sx={{ mb: 1.5 }}>
          {t("dashboard.optional_modules", {
            defaultValue: "Optional Modules",
          })}
        </Typography>
        <Stack direction="row" gap={1} flexWrap="wrap" useFlexGap>
          {services.map((svc) => (
            <Chip
              key={svc.name}
              label={`${t(`modules.${svc.name}.name`, { defaultValue: svc.display_name })}: ${svc.enabled ? t(`modules.status.${svc.status}`, { defaultValue: svc.status }) : t("modules.status.disabled", { defaultValue: "disabled" })}`}
              color={svc.enabled ? moduleStatusColor(svc.status) : "default"}
              variant="outlined"
              size="small"
            />
          ))}
        </Stack>
      </CardContent>
    </Card>
  );
}
