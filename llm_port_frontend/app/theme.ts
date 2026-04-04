import { alpha, createTheme } from "@mui/material/styles";
import type { PaletteMode } from "@mui/material";

export function getAppTheme(mode: PaletteMode) {
  const dark = mode === "dark";

  return createTheme({
    palette: {
      mode,
      primary: {
        main: "#7c4dff",
        light: "#b47cff",
        dark: "#3f1dcb",
      },
      secondary: {
        main: "#00b8d4",
        light: "#6effff",
        dark: "#0088a3",
      },
      background: dark
        ? {
            default: "#0a0e1a",
            paper: "#111827",
          }
        : {
            default: "#f5f7fb",
            paper: "#ffffff",
          },
      error: { main: "#e53935" },
      warning: { main: "#fb8c00" },
      success: { main: "#2e7d32" },
      text: dark
        ? {
            primary: "#e0e0e0",
            secondary: "#9e9e9e",
          }
        : {
            primary: "#1a1f2f",
            secondary: "#4b5565",
          },
      divider: dark ? "rgba(255,255,255,0.08)" : "rgba(18,28,45,0.12)",
    },
    typography: {
      fontFamily: '"Inter", "Roboto", "Helvetica", "Arial", sans-serif',
      h4: { fontWeight: 700 },
      h5: { fontWeight: 700 },
      h6: { fontWeight: 600 },
    },
    shape: {
      borderRadius: 12,
    },
    components: {
      MuiDrawer: {
        styleOverrides: {
          paper: ({ theme }) => ({
            backgroundColor: theme.palette.background.paper,
            borderRight: `1px solid ${theme.palette.divider}`,
          }),
        },
      },
      MuiAppBar: {
        styleOverrides: {
          root: ({ theme }) => ({
            backgroundColor: theme.palette.background.paper,
            borderBottom: `1px solid ${theme.palette.divider}`,
          }),
        },
      },
      MuiTableHead: {
        styleOverrides: {
          root: ({ theme }) => ({
            "& .MuiTableCell-head": {
              backgroundColor: dark ? "#171f32" : "#eef0f6",
              color: theme.palette.text.secondary,
              fontWeight: 600,
              fontSize: "0.75rem",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              position: "relative",
            },
          }),
        },
      },
      MuiTableRow: {
        styleOverrides: {
          root: ({ theme }) => ({
            "&:hover": {
              backgroundColor: `${alpha(theme.palette.primary.main, dark ? 0.08 : 0.06)} !important`,
            },
          }),
        },
      },
      MuiTableCell: {
        styleOverrides: {
          root: ({ theme }) => ({
            borderColor: theme.palette.divider,
          }),
        },
      },
      MuiPaper: {
        styleOverrides: {
          root: {
            backgroundImage: "none",
          },
        },
      },
      MuiButton: {
        styleOverrides: {
          root: {
            textTransform: "none",
            fontWeight: 600,
          },
        },
      },
      MuiChip: {
        styleOverrides: {
          root: {
            fontWeight: 600,
          },
        },
      },
    },
  });
}
