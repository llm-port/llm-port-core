import { useMemo, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import InputAdornment from "@mui/material/InputAdornment";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import SearchIcon from "@mui/icons-material/Search";

import UsersPage from "~/pages/admin/UsersPage";
import {
  getAdminGeneralSettings,
  saveAdminGeneralSettings,
  type AdminGeneralSettings,
} from "~/lib/adminSettings";

type SettingsTab = "general" | "users";

function getCurrentTab(pathname: string, tabQuery: string | null): SettingsTab {
  if (pathname === "/admin/users") return "users";
  if (tabQuery === "general") return "general";
  if (tabQuery === "users") return "users";
  return "general";
}

interface GeneralSettingItem {
  key: "apiServer.endpointUrl" | "apiServer.containerName";
  category: "api_server";
  label: string;
  description: string;
  value: string;
}

function containsSearch(text: string, query: string): boolean {
  return text.toLowerCase().includes(query.toLowerCase());
}

export default function SettingsPage() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [search, setSearch] = useState("");
  const [generalSettings, setGeneralSettings] = useState<AdminGeneralSettings>(() => getAdminGeneralSettings());

  const tab = useMemo(
    () => getCurrentTab(location.pathname, searchParams.get("tab")),
    [location.pathname, searchParams],
  );

  const generalItems = useMemo<GeneralSettingItem[]>(
    () => [
      {
        key: "apiServer.endpointUrl",
        category: "api_server",
        label: t("settings.general.api_server.endpoint_url"),
        description: t("settings.general.api_server.endpoint_url_desc"),
        value: generalSettings.apiServer.endpointUrl,
      },
      {
        key: "apiServer.containerName",
        category: "api_server",
        label: t("settings.general.api_server.container_name"),
        description: t("settings.general.api_server.container_name_desc"),
        value: generalSettings.apiServer.containerName,
      },
    ],
    [generalSettings.apiServer.containerName, generalSettings.apiServer.endpointUrl, t],
  );

  const filteredGroups = useMemo(() => {
    const trimmed = search.trim().toLowerCase();
    const filtered = trimmed
      ? generalItems.filter((item) =>
          containsSearch(`${item.label} ${item.description} ${item.value}`, trimmed),
        )
      : generalItems;
    return {
      api_server: filtered.filter((item) => item.category === "api_server"),
    };
  }, [generalItems, search]);

  function handleTabChange(_event: React.SyntheticEvent, nextTab: SettingsTab) {
    navigate(`/admin/settings?tab=${nextTab}`, { replace: true });
  }

  function updateGeneralField(
    key: "endpointUrl" | "containerName",
    value: string,
  ) {
    const nextSettings: AdminGeneralSettings = {
      ...generalSettings,
      apiServer: {
        ...generalSettings.apiServer,
        [key]: value,
      },
    };
    setGeneralSettings(nextSettings);
    saveAdminGeneralSettings(nextSettings);
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", minHeight: 0, height: "100%" }}>
      <Box sx={{ mb: 2 }}>
        <Typography variant="h5" sx={{ mb: 1 }}>
          {t("settings.title")}
        </Typography>
        <Tabs value={tab} onChange={handleTabChange}>
          <Tab label={t("settings.general.title")} value="general" />
          <Tab label={t("users.title")} value="users" />
        </Tabs>
      </Box>

      <Box sx={{ minHeight: 0, flexGrow: 1, display: "flex", flexDirection: "column" }}>
        {tab === "general" && (
          <Stack spacing={2}>
            <TextField
              size="small"
              fullWidth
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t("settings.search_placeholder")}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon fontSize="small" />
                  </InputAdornment>
                ),
              }}
            />

            {filteredGroups.api_server.length > 0 && (
              <Paper sx={{ p: 2 }}>
                <Typography variant="subtitle1" sx={{ mb: 1 }}>
                  {t("settings.general.api_server.group_title")}
                </Typography>
                <Stack spacing={1.5}>
                  {filteredGroups.api_server.some((item) => item.key === "apiServer.endpointUrl") && (
                    <TextField
                      size="small"
                      fullWidth
                      label={t("settings.general.api_server.endpoint_url")}
                      helperText={t("settings.general.api_server.endpoint_url_desc")}
                      value={generalSettings.apiServer.endpointUrl}
                      onChange={(event) => updateGeneralField("endpointUrl", event.target.value)}
                    />
                  )}
                  {filteredGroups.api_server.some((item) => item.key === "apiServer.containerName") && (
                    <TextField
                      size="small"
                      fullWidth
                      label={t("settings.general.api_server.container_name")}
                      helperText={t("settings.general.api_server.container_name_desc")}
                      value={generalSettings.apiServer.containerName}
                      onChange={(event) => updateGeneralField("containerName", event.target.value)}
                    />
                  )}
                </Stack>
              </Paper>
            )}
            {filteredGroups.api_server.length === 0 && (
              <Typography color="text.secondary">{t("settings.no_results")}</Typography>
            )}
          </Stack>
        )}
        {tab === "users" && <UsersPage />}
      </Box>
    </Box>
  );
}
