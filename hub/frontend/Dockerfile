FROM nginx:1.27-alpine
# Build context is the repo root (see docker-compose.yml) so this can pull in
# both the main app (root frontend/ -- shared, unchanged, with the local
# standalone server) and the hub-only auth pages (hub/frontend/).
COPY frontend/. /usr/share/nginx/html/
COPY hub/frontend/. /usr/share/nginx/html/
