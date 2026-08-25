FROM mcr.microsoft.com/playwright:v1.62.1-noble

WORKDIR /opt/val-browser
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force
COPY run.mjs ./

ENTRYPOINT ["node", "/opt/val-browser/run.mjs"]
