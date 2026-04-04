/**
 * Custom LLM icon — a stylised brain-circuit icon for the sidebar.
 * Renders as a standard MUI SvgIcon so it works with ListItemIcon, etc.
 */
import SvgIcon, { type SvgIconProps } from "@mui/material/SvgIcon";

export default function LlmIcon(props: SvgIconProps) {
  return (
    <SvgIcon {...props} viewBox="0 0 24 24">
      {/* Outer brain-like shape */}
      <path
        d="M12 2C9.5 2 7.4 3.3 6.3 5.3 4.9 5.8 3.8 6.9 3.3 8.3 2.5 9.5 2 11 2 12.5c0 1.8.7 3.4 1.8 4.6.1 1.5.8 2.8 1.9 3.8 1.1 1 2.6 1.6 4.1 1.6h4.4c1.5 0 3-.6 4.1-1.6 1.1-1 1.8-2.3 1.9-3.8C21.3 16 22 14.3 22 12.5c0-1.5-.5-3-1.3-4.2-.5-1.4-1.6-2.5-3-3C16.6 3.3 14.5 2 12 2z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      {/* Circuit nodes */}
      <circle cx="9" cy="9" r="1.3" fill="currentColor" />
      <circle cx="15" cy="9" r="1.3" fill="currentColor" />
      <circle cx="12" cy="14" r="1.3" fill="currentColor" />
      {/* Connections */}
      <line x1="9" y1="9" x2="15" y2="9" stroke="currentColor" strokeWidth="1" />
      <line x1="9" y1="9" x2="12" y2="14" stroke="currentColor" strokeWidth="1" />
      <line x1="15" y1="9" x2="12" y2="14" stroke="currentColor" strokeWidth="1" />
      {/* Pulse dot at center */}
      <circle cx="12" cy="10.5" r="0.7" fill="currentColor" opacity="0.5" />
    </SvgIcon>
  );
}
