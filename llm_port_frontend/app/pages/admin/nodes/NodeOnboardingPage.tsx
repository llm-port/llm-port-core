import { useState } from "react";
import { nodesApi, type NodeEnrollmentToken } from "~/api/nodes";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Drawer from "@mui/material/Drawer";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import CloseIcon from "@mui/icons-material/Close";

interface NodeOnboardingDrawerProps {
  open: boolean;
  onClose: () => void;
}

export default function NodeOnboardingDrawer({
  open,
  onClose,
}: NodeOnboardingDrawerProps) {
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState<NodeEnrollmentToken | null>(null);

  async function createToken() {
    setLoading(true);
    setError(null);
    try {
      setToken(await nodesApi.createEnrollmentToken(note));
    } catch (err: unknown) {
      setError(
        err instanceof Error
          ? err.message
          : "Failed to create enrollment token.",
      );
    } finally {
      setLoading(false);
    }
  }

  async function copyToken() {
    if (!token) return;
    try {
      await navigator.clipboard.writeText(token.token);
    } catch {
      setError("Failed to copy token to clipboard.");
    }
  }

  function handleClose() {
    setNote("");
    setError(null);
    setToken(null);
    onClose();
  }

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={handleClose}
      PaperProps={{ sx: { width: { xs: "100%", sm: 480 }, p: 3 } }}
    >
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 2 }}
      >
        <Typography variant="h6">Node Onboarding</Typography>
        <IconButton onClick={handleClose} size="small" aria-label="close">
          <CloseIcon />
        </IconButton>
      </Stack>

      <Card variant="outlined" sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="subtitle1" sx={{ mb: 1 }}>
            Generate Enrollment Token
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Token is single-use and exchanged by a new node agent for long-lived
            credentials.
          </Typography>
          <Stack spacing={1}>
            <TextField
              label="Note"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              fullWidth
              size="small"
              placeholder="Optional host or rack note"
            />
            <Button
              variant="contained"
              onClick={createToken}
              disabled={loading}
              fullWidth
            >
              {loading ? "Creating..." : "Create Token"}
            </Button>
          </Stack>
          {error && (
            <Alert severity="error" sx={{ mt: 2 }}>
              {error}
            </Alert>
          )}
        </CardContent>
      </Card>

      {token && (
        <Card variant="outlined" sx={{ mb: 2 }}>
          <CardContent>
            <Typography variant="subtitle1" sx={{ mb: 1 }}>
              Enrollment Token
            </Typography>
            <Alert severity="warning" sx={{ mb: 2 }}>
              This value is shown once. Store it securely and pass it to the
              node agent installer.
            </Alert>
            <TextField
              value={token.token}
              fullWidth
              size="small"
              slotProps={{ input: { readOnly: true } }}
            />
            <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
              <Button variant="outlined" onClick={copyToken}>
                Copy Token
              </Button>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ alignSelf: "center" }}
              >
                Expires at: {new Date(token.expires_at).toLocaleString()}
              </Typography>
            </Stack>
          </CardContent>
        </Card>
      )}

      <Card variant="outlined">
        <CardContent>
          <Typography variant="subtitle1" sx={{ mb: 1 }}>
            Agent Enrollment Steps
          </Typography>
          <Box
            component="pre"
            sx={{
              fontFamily: "monospace",
              fontSize: "0.8rem",
              m: 0,
              whiteSpace: "pre-wrap",
            }}
          >
            {`1. Install llm_port_node_agent on the target host.
2. Set agent_id, host, and the enrollment token.
3. Start the agent service.
4. Agent calls POST /api/admin/system/nodes/enroll.
5. Agent stores credential and opens the stream.`}
          </Box>
        </CardContent>
      </Card>
    </Drawer>
  );
}
