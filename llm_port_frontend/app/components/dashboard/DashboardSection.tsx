import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Collapse from "@mui/material/Collapse";
import Tooltip from "@mui/material/Tooltip";
import DragIndicatorIcon from "@mui/icons-material/DragIndicator";
import VisibilityIcon from "@mui/icons-material/Visibility";
import VisibilityOffIcon from "@mui/icons-material/VisibilityOff";
import type { SectionId } from "~/lib/useDashboardLayout";

interface DashboardSectionProps {
  id: SectionId;
  title: string;
  hidden: boolean;
  editMode: boolean;
  onToggleVisibility: (id: SectionId) => void;
  children: React.ReactNode;
}

export default function DashboardSection({
  id,
  title,
  hidden,
  editMode,
  onToggleVisibility,
  children,
}: DashboardSectionProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id, disabled: !editMode });

  return (
    <Box
      ref={setNodeRef}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.35 : 1,
      }}
      {...attributes}
      sx={{
        position: "relative",
        "&:hover .section-drag-handle": editMode ? { opacity: 0.6 } : undefined,
      }}
    >
      {/* Edit-mode header bar */}
      {editMode && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.5}
          sx={{
            mb: hidden ? 0 : 0.5,
            py: 0.5,
            px: 1,
            borderRadius: 1,
            bgcolor: hidden ? "action.disabledBackground" : "action.hover",
          }}
        >
          <Box
            ref={setActivatorNodeRef}
            {...listeners}
            className="section-drag-handle"
            sx={{
              display: { xs: "none", sm: "flex" },
              alignItems: "center",
              cursor: "grab",
              opacity: 0.3,
              transition: "opacity 0.15s",
              "&:hover": { opacity: "1 !important" },
            }}
          >
            <DragIndicatorIcon sx={{ fontSize: 18, color: "text.disabled" }} />
          </Box>
          <Typography
            variant="body2"
            sx={{
              fontWeight: 600,
              flexGrow: 1,
              color: hidden ? "text.disabled" : "text.primary",
            }}
          >
            {title}
          </Typography>
          <Tooltip title={hidden ? "Show section" : "Hide section"}>
            <IconButton
              size="small"
              onClick={() => onToggleVisibility(id)}
              sx={{ p: 0.25 }}
            >
              {hidden ? (
                <VisibilityOffIcon sx={{ fontSize: 18 }} />
              ) : (
                <VisibilityIcon sx={{ fontSize: 18 }} />
              )}
            </IconButton>
          </Tooltip>
        </Stack>
      )}

      {/* Content — collapsed when hidden */}
      <Collapse in={!hidden} unmountOnExit>
        {children}
      </Collapse>
    </Box>
  );
}
