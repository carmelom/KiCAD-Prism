# Frontend Dockerfile - Multi-stage build

# Stage 1: Build the React application
FROM node:20-alpine AS builder

WORKDIR /app

# Copy package files and install dependencies
COPY package*.json ./
RUN npm ci --include=dev

# Copy source and build
COPY . .
RUN npm run build

# Stage 2: Serve with Nginx
FROM nginx:alpine

# Copy built assets from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy custom nginx config for SPA routing and API proxy.
# Map lives in a separate conf.d file (http context) so $prism_forwarded_proto
# can prefer the outer X-Forwarded-Proto over the internal HTTP $scheme.
COPY nginx-forwarded-map.conf /etc/nginx/conf.d/00-forwarded-map.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expose port 80
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
