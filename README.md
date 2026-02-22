# Welcome to React Router!

A modern, production-ready template for building full-stack React applications using React Router.

[![Open in StackBlitz](https://developer.stackblitz.com/img/open_in_stackblitz.svg)](https://stackblitz.com/github/remix-run/react-router-templates/tree/main/default)

## Features

- 🚀 Server-side rendering
- ⚡️ Hot Module Replacement (HMR)
- 📦 Asset bundling and optimization
- 🔄 Data loading and mutations
- 🔒 TypeScript by default
- 🎉 TailwindCSS for styling
- 📖 [React Router docs](https://reactrouter.com/)

## Getting Started

### Installation

Install the dependencies:

```bash
npm install
```

### Development

Start the development server with HMR:

```bash
npm run dev
```

Your application will be available at `http://localhost:5173`.

## Building for Production

Create a production build:

```bash
npm run build
```

## Deployment

### Docker Deployment

To build and run using Docker:

```bash
docker build -t my-app .

# Run the container
docker run -p 3000:3000 my-app
```

The containerized application can be deployed to any platform that supports Docker, including:

- AWS ECS
- Google Cloud Run
- Azure Container Apps
- Digital Ocean App Platform
- Fly.io
- Railway

### DIY Deployment

If you're familiar with deploying Node applications, the built-in app server is production-ready.

Make sure to deploy the output of `npm run build`

```
├── package.json
├── package-lock.json (or pnpm-lock.yaml, or bun.lockb)
├── build/
│   ├── client/    # Static assets
│   └── server/    # Server-side code
```

## Styling

This template comes with [Tailwind CSS](https://tailwindcss.com/) already configured for a simple default starting experience. You can use whatever CSS framework you prefer.

---

Built with ❤️ using React Router.

## Admin Logs Page

- Primary route: `/admin/logs`
- Tabs:
  - `Logs` (Loki query + live tail through backend `/api/logs/*`)
  - `Audit` (existing audit log table)
- Legacy route `/admin/audit` now redirects to `/admin/logs?tab=audit`.

## Runtime i18n

- Frontend uses `react-i18next` with HTTP backend.
- Bundles are loaded from:
  - `/api/i18n/languages`
  - `/api/i18n/{lang}/{namespace}`
- Language selection is available in the admin top bar and persisted in `localStorage` (`llm-port-lang`).
- New languages can be added on backend bundle files without rebuilding frontend assets.

## LLM Graph Visualizer

- New route: `/admin/llm/graph`
- Built with `reactflow` and rendered as read-only topology + live traces view.
- Data source is backend-only:
  - `GET /api/llm/graph/topology`
  - `GET /api/llm/graph/traces`
  - `GET /api/llm/graph/traces/stream` (SSE)
- Frontend never calls Langfuse directly and does not contain Langfuse credentials.

## LLM Endpoint Admin Page

- Route: `/admin/llm/endpoint`
- Embeds `llm_port_api` Swagger UI and includes container lifecycle controls (`Start`, `Stop`, `Restart`).
- Uses backend container admin APIs only (no direct Docker access from browser).
- Optional frontend env vars:
  - `VITE_LLM_PORT_API_DOCS_URL` (default `http://localhost:8001/api/docs`)
  - `VITE_LLM_PORT_API_CONTAINER_NAME` (default `llm-port-api`)

## Breaking Rename Migration (`airgap` -> `llm-port`)

- Frontend branding is now `llm-port`.
- Local storage keys changed:
  - `airgap-lang` -> `llm-port-lang`
  - `airgap-theme-mode` -> `llm-port-theme-mode`
- Existing browsers may need one fresh login/theme/language re-selection after upgrade.
