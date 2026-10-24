# React 19 dashboard — two targets:
#   `runtime` (default) builds the static bundle and serves it with nginx (port 80)
#   `dev`     runs the Vite dev server (port 5173), used by docker-compose for live editing
#
# `apps/web` is a self-contained pnpm project (its own lockfile), but the build context is the
# repository root: the Demo Video tab embeds docs/media/harvestgrid-demo.webm, which the Vite media
# plugin copies into `dist/media/`. Building from `apps/web` alone would ship a console whose video
# tab reports the recording as missing.

FROM node:22-alpine AS deps
WORKDIR /repo/apps/web
RUN corepack enable
COPY apps/web/package.json apps/web/pnpm-lock.yaml ./
RUN corepack pnpm install --frozen-lockfile


FROM deps AS build
COPY apps/web ./
# Recorded walkthrough + cue sheet, published at <base>media/ by vite.config.ts.
COPY docs/media /repo/docs/media
# Demo mode: the dashboard talks to the in-browser fixture store instead of the API, so the static
# bundle needs no backend and no CORS. Set VITE_DEMO_MODE=false for the live-API variant.
ARG VITE_DEMO_MODE=true
ARG VITE_BASE_PATH=/
ENV VITE_DEMO_MODE=${VITE_DEMO_MODE} \
    VITE_BASE_PATH=${VITE_BASE_PATH}
RUN corepack pnpm build


FROM nginx:1.27-alpine AS runtime
COPY --from=build /repo/apps/web/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=6 \
  CMD wget --quiet --tries=1 --spider http://127.0.0.1/ || exit 1
CMD ["nginx", "-g", "daemon off;"]


FROM deps AS dev
COPY apps/web ./
COPY docs/media /repo/docs/media
ENV VITE_DEMO_MODE=${VITE_DEMO_MODE:-true} \
    VITE_BASE_PATH=${VITE_BASE_PATH:-/}
EXPOSE 5173
CMD ["corepack", "pnpm", "dev", "--host", "0.0.0.0", "--port", "5173"]
