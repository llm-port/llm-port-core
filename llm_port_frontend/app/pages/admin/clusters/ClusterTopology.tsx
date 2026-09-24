/**
 * The cluster, drawn.
 *
 * Head at the centre, machines around it, one edge per resolved fabric
 * binding labelled with the link that actually carries traffic. Colours come
 * from theme tokens rather than literals so the picture reads on either
 * ground.
 *
 * The picture moves, and only where something is actually happening. A live
 * machine's ring breathes; a link Ray reports as carrying traffic has a pulse
 * running head-ward along it. A still diagram and a dead cluster looked
 * identical, which is the one distinction this view exists to make -- so
 * motion here is not decoration, it is the reading.
 *
 * Nothing animates for a machine that is not reporting, a link that is not
 * up, or a viewer who has asked their system for reduced motion.
 */
import { useTranslation } from "react-i18next";
import { useTheme } from "@mui/material/styles";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { useReducedMotion } from "~/lib/useReducedMotion";
import { layoutTopology, nodeTone, type Topology } from "./topology";
import type { EnvironmentNode, InferenceEnvironment } from "~/api/inference";
import type { ManagedNode } from "~/api/nodes";

export interface ClusterTopologyProps {
  cluster: InferenceEnvironment;
  members: EnvironmentNode[];
  nodes: ManagedNode[];
  selectedNodeId?: string | null;
  onSelect?: (nodeId: string) => void;
}

export function ClusterTopology({
  cluster,
  members,
  nodes,
  selectedNodeId,
  onSelect,
}: ClusterTopologyProps) {
  const theme = useTheme();
  const { t } = useTranslation();
  const roleLabel = (role: string) =>
    role === "head" || role === "worker" ? t(`clusters.role.${role}`) : role;
  const topology: Topology = layoutTopology(cluster, members, nodes);
  // Honour the system setting rather than deciding for the operator. SVG
  // animation cannot be reached by a media query, so it is gated here.
  const still = useReducedMotion();

  const tones = {
    good: theme.palette.success.main,
    warn: theme.palette.warning.main,
    bad: theme.palette.error.main,
    idle: theme.palette.text.disabled,
  } as const;

  if (topology.nodes.length === 0) {
    return (
      <Box sx={{ p: 4, textAlign: "center" }}>
        <Typography variant="body2" color="text.secondary">
          {t("clusters.topology.empty")}
        </Typography>
      </Box>
    );
  }

  return (
    <Box>
      <Box
        component="svg"
        role="img"
        aria-label={t("clusters.topology.aria")}
        viewBox={`0 0 ${topology.width} ${topology.height}`}
        sx={{ width: "100%", height: "auto", display: "block", maxWidth: "100%" }}
      >
        {topology.edges.map((edge) => {
          const from = topology.nodes.find((n) => n.nodeId === edge.from);
          const to = topology.nodes.find((n) => n.nodeId === edge.to);
          if (!from || !to) return null;
          return (
            <g key={`${edge.from}-${edge.to}`}>
              <line
                x1={from.x}
                y1={from.y}
                x2={to.x}
                y2={to.y}
                stroke={edge.active ? theme.palette.primary.main : theme.palette.divider}
                strokeWidth={edge.active ? 2 : 1.5}
                strokeDasharray={edge.active ? undefined : "5 5"}
              />
              {/* A live link carries something. The dot runs worker -> head
                  because that is the direction the work reports back in. */}
              {edge.active && !still && (
                <circle r={3.5} fill={theme.palette.primary.main}>
                  <animate
                    attributeName="cx"
                    values={`${to.x};${from.x}`}
                    dur="2.4s"
                    repeatCount="indefinite"
                  />
                  <animate
                    attributeName="cy"
                    values={`${to.y};${from.y}`}
                    dur="2.4s"
                    repeatCount="indefinite"
                  />
                  <animate
                    attributeName="opacity"
                    values="0;1;1;0"
                    keyTimes="0;0.15;0.85;1"
                    dur="2.4s"
                    repeatCount="indefinite"
                  />
                </circle>
              )}
              {edge.label && (
                <text
                  x={(from.x + to.x) / 2}
                  y={(from.y + to.y) / 2 - 6}
                  textAnchor="middle"
                  fontSize="10"
                  fontFamily="monospace"
                  fill={theme.palette.text.secondary}
                >
                  {edge.label}
                </text>
              )}
            </g>
          );
        })}

        {topology.nodes.map((node) => {
          const tone = tones[nodeTone(node)];
          const selected = selectedNodeId === node.nodeId;
          const live = (node.memberStatus ?? "").toLowerCase() === "alive";
          return (
            <g
              key={node.nodeId}
              transform={`translate(${node.x},${node.y})`}
              onClick={() => onSelect?.(node.nodeId)}
              style={{ cursor: onSelect ? "pointer" : "default" }}
              tabIndex={onSelect ? 0 : undefined}
              role={onSelect ? "button" : undefined}
              aria-label={`${node.host}, ${roleLabel(node.role)}`}
              data-address={node.address}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelect?.(node.nodeId);
                }
              }}
            >
              {/* The halo. Keyed on compute, not on committed memory: a
                  replica reserves most of its card the moment it starts, so
                  memory would halo a model that is sitting there idle. */}
              {live && !still && (node.activity ?? 0) > 0.02 && (
                <circle r={node.r} fill="none" stroke={tone} strokeWidth={2}>
                  <animate
                    attributeName="r"
                    values={`${node.r};${node.r + 14}`}
                    dur="2s"
                    repeatCount="indefinite"
                  />
                  <animate
                    attributeName="stroke-opacity"
                    values="0.45;0"
                    dur="2s"
                    repeatCount="indefinite"
                  />
                </circle>
              )}
              <circle
                r={node.r}
                fill={theme.palette.background.paper}
                stroke={tone}
                strokeWidth={selected ? 3.5 : 2}
              >
                {/* The ring itself breathes while the machine is alive. This
                    is the "it is running" signal: it needs no allocation
                    number and it is visible at a glance across the page. */}
                {live && !still && (
                  <animate
                    attributeName="stroke-opacity"
                    values="1;0.45;1"
                    dur="3s"
                    repeatCount="indefinite"
                  />
                )}
              </circle>
              {/* Allocation arc: how much of the machine is committed. */}
              {node.allocation !== null && (
                <circle
                  r={node.r + 5}
                  fill="none"
                  stroke={tone}
                  strokeWidth={3}
                  strokeOpacity={0.55}
                  strokeDasharray={`${2 * Math.PI * (node.r + 5) * node.allocation} ${2 * Math.PI * (node.r + 5)}`}
                  transform="rotate(-90)"
                />
              )}
              <text
                textAnchor="middle"
                y={node.isHead ? -4 : -2}
                fontSize={node.isHead ? 13 : 11}
                fontWeight={600}
                fill={theme.palette.text.primary}
              >
                {node.host}
              </text>
              <text
                textAnchor="middle"
                y={node.isHead ? 13 : 12}
                fontSize="9.5"
                fill={theme.palette.text.secondary}
              >
                {node.isHead ? t("clusters.topology.leads") : roleLabel(node.role)}
              </text>
              {node.allocation !== null && (
                <text
                  textAnchor="middle"
                  y={node.isHead ? 28 : 24}
                  fontSize="9.5"
                  fill={theme.palette.text.secondary}
                >
                  {t("clusters.topology.in_use", { pct: Math.round(node.allocation * 100) })}
                </text>
              )}
            </g>
          );
        })}
      </Box>

      <Stack direction="row" spacing={2} sx={{ mt: 1, flexWrap: "wrap" }}>
        {[
          [t("clusters.topology.legend_healthy"), tones.good],
          [t("clusters.topology.legend_busy"), tones.warn],
          // Reachable now that the cluster observation is written back onto
          // each member; before that every machine read "not reporting".
          [t("clusters.topology.legend_dropped"), tones.bad],
          [t("clusters.topology.legend_silent"), tones.idle],
        ].map(([label, colour]) => (
          <Stack key={label} direction="row" spacing={0.75} alignItems="center">
            <Box
              sx={{
                width: 9,
                height: 9,
                borderRadius: "50%",
                bgcolor: colour as string,
              }}
            />
            <Typography variant="caption" color="text.secondary">
              {label}
            </Typography>
          </Stack>
        ))}
        {!still && (
          <Typography variant="caption" color="text.disabled">
            {t("clusters.topology.legend_motion")}
          </Typography>
        )}
      </Stack>
    </Box>
  );
}
