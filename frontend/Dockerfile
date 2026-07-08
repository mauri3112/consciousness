FROM node:22-alpine AS build

WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ARG VITE_CONSCIOUSNESS_API_URL=http://localhost:8770
ENV VITE_CONSCIOUSNESS_API_URL=$VITE_CONSCIOUSNESS_API_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
