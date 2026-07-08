# --- build stage: compile the SPA ---
FROM node:20-alpine AS build
WORKDIR /app

# Install deps first for layer caching. --legacy-peer-deps: openapi-typescript's
# peer range vs the installed TS (see docs/frontend/README invariants).
COPY package.json package-lock.json ./
RUN npm ci --legacy-peer-deps

COPY . .
RUN npm run build          # tsc -b && vite build -> /app/dist

# --- serve stage: static SPA behind nginx (also reverse-proxies /api) ---
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
