FROM node:25-alpine AS deps
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN npm install -g pnpm@9
RUN pnpm install --frozen-lockfile

FROM node:25-alpine AS builder
WORKDIR /app
RUN npm install -g pnpm@9
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# No NEXT_PUBLIC_API_BASE_URL is baked in: the dashboard and API share an origin
# (dashboard at /, backend at /api), so the client calls the relative /api path.
RUN pnpm run build

FROM node:25-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
# curl is used by the compose healthcheck.
RUN apk add --no-cache curl
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001 -G nodejs
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public
USER nextjs
EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"
CMD ["node", "server.js"]

