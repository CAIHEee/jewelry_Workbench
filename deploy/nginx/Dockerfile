FROM node:22-alpine AS frontend-builder

WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm install

COPY shared/ /app/shared/
COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine

COPY deploy/nginx/jinma.docker.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-builder /app/frontend/dist /usr/share/nginx/html
