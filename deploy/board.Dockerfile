# SenseBoard: Vite build -> static files served by nginx (nginx.conf proxies /v1 and /edge).
FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json* ./
COPY apps/senseboard/package.json apps/senseboard/
COPY packages/contracts/ts packages/contracts/ts
RUN npm install --workspace apps/senseboard
COPY apps/senseboard apps/senseboard
# Build-time URLs: in compose the browser talks to the published host ports.
ARG VITE_EDGE_URL=http://localhost:8001
ARG VITE_CLOUD_URL=http://localhost:8000
ARG VITE_STORE_ID=STR-DL-001
ENV VITE_EDGE_URL=$VITE_EDGE_URL VITE_CLOUD_URL=$VITE_CLOUD_URL VITE_STORE_ID=$VITE_STORE_ID
RUN npm run build --workspace apps/senseboard

FROM nginx:1.27-alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/apps/senseboard/dist /usr/share/nginx/html
EXPOSE 80
