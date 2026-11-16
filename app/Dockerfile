# syntax=docker/dockerfile:1
# The scenario assistant for Azure Container Apps, built from app/ as the context. The build stage builds the React
# app and bundles the Node.js server into one module; the runtime stage runs that bundle on a distroless, non-root
# Node.js image without a shell, package manager or node_modules. deploy.py builds it with ACR Tasks.
FROM node:24-alpine AS build
# Optional npm mirror for networks that block registry.npmjs.org; npm routes the lockfile's URLs through it.
ARG NPM_REGISTRY=""
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund ${NPM_REGISTRY:+--registry=$NPM_REGISTRY}
COPY . .
RUN npm run build

FROM gcr.io/distroless/nodejs24-debian13:nonroot
WORKDIR /srv
ENV NODE_ENV=production \
    PORT=8080
COPY --from=build /src/dist dist
COPY --from=build /src/dist-server dist-server
EXPOSE 8080
CMD ["dist-server/main.mjs"]
