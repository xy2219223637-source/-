ARG NODE_BASE_IMAGE=docker.m.daocloud.io/library/node:22-alpine
ARG NGINX_BASE_IMAGE=docker.m.daocloud.io/library/nginx:1.27-alpine

FROM ${NODE_BASE_IMAGE} AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM ${NGINX_BASE_IMAGE}
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
