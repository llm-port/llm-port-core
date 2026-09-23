import type { Config } from "@react-router/dev/config";

export default {
  // Config options...
  // Server-side render by default, to enable SPA mode set this to `false`
  ssr: true,
  // Ship the route table with the first page load. The default ("lazy")
  // asks the server for it whenever a path has not been seen -- and every new
  // chat is a path never seen before (/chat/<new id>), so starting a chat
  // waited on a /__manifest round trip before its first message could go.
  // The table is a few dozen routes: paths and module URLs, not code.
  routeDiscovery: { mode: "initial" },
} satisfies Config;
