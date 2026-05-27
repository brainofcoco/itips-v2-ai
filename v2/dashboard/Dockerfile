# Two-stage build so the runtime image carries only static files +
# nginx, not the full node toolchain. Strip the dashboard service from
# docker-compose for Jetson production and this image is never built.

# ─── stage 1: build the bundle ────────────────────────────────────────
FROM node:20-alpine AS build

WORKDIR /app

# Cache node_modules across rebuilds: copy manifests first.
COPY package.json package-lock.json* pnpm-lock.yaml* yarn.lock* ./
RUN if [ -f pnpm-lock.yaml ]; then \
        corepack enable && pnpm install --frozen-lockfile; \
    elif [ -f yarn.lock ]; then \
        corepack enable && yarn install --frozen-lockfile; \
    else \
        npm install --no-audit --no-fund; \
    fi

COPY tsconfig.json tsconfig.node.json vite.config.ts index.html ./
COPY src ./src

# VITE_API_TARGET is only consulted by the dev server; the production
# build's API calls go through nginx's proxy_pass below, so this is a
# no-op at build time.
RUN npm run build


# ─── stage 2: nginx serves dist/ and proxies /api etc. to itips ───────
FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

# nginx already daemonizes; the default CMD is fine.
