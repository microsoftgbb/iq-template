FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# The server uses only Node built-ins, so the runtime image carries no npm dependencies.
FROM node:24-alpine
ENV NODE_ENV=production PORT=8080
WORKDIR /app
COPY --from=build /app/dist ./dist
COPY server ./server
USER node
EXPOSE 8080
CMD ["node", "server/server.mjs"]
