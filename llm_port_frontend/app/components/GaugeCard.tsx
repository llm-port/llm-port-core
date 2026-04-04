import { useTheme } from "@mui/material/styles";
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Typography from "@mui/material/Typography";
import { Gauge, gaugeClasses } from "@mui/x-charts/Gauge";

/** Color thresholds: <=60 success, <=85 warning, >85 error */
function arcColor(value: number, theme: ReturnType<typeof useTheme>): string {
  if (value <= 60) return theme.palette.success.main;
  if (value <= 85) return theme.palette.warning.main;
  return theme.palette.error.main;
}

export interface GaugeCardProps {
  /** Card title (e.g. "CPU") */
  label: string;
  /** 0-100 percentage value, or null if unavailable */
  value: number | null;
  /** Primary detail line below the gauge */
  detail?: string;
  /** Secondary detail line */
  secondaryDetail?: string;
  /** Override inner text (defaults to formatted percent) */
  innerText?: string;
}

export default function GaugeCard({
  label,
  value,
  detail,
  secondaryDetail,
  innerText,
}: GaugeCardProps) {
  const theme = useTheme();
  const { t } = useTranslation();
  const pct = value ?? 0;
  const color = arcColor(pct, theme);

  return (
    <Card variant="outlined" sx={{ height: "100%" }}>
      <CardContent
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 0.5,
          py: 1.5,
          "&:last-child": { pb: 1.5 },
        }}
      >
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ fontWeight: 600 }}
        >
          {label}
        </Typography>

        {value == null ? (
          <Box
            sx={{
              height: 110,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Typography variant="h6" color="text.secondary">
              {t("nodes.not_available")}
            </Typography>
          </Box>
        ) : (
          <Gauge
            value={pct}
            startAngle={-110}
            endAngle={110}
            width={130}
            height={110}
            innerRadius="72%"
            outerRadius="100%"
            text={innerText ?? `${pct.toFixed(1)}%`}
            sx={{
              [`& .${gaugeClasses.valueArc}`]: {
                fill: color,
              },
              [`& .${gaugeClasses.referenceArc}`]: {
                fill: theme.palette.divider,
              },
              [`& .${gaugeClasses.valueText}`]: {
                fontSize: 18,
                fontWeight: 700,
                fill: theme.palette.text.primary,
              },
            }}
          />
        )}

        {detail && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ textAlign: "center", lineHeight: 1.3 }}
          >
            {detail}
          </Typography>
        )}
        {secondaryDetail && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ textAlign: "center", lineHeight: 1.3, mt: -0.25 }}
          >
            {secondaryDetail}
          </Typography>
        )}
      </CardContent>
    </Card>
  );
}
