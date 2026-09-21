FROM node:24-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates procps curl \
    && rm -rf /var/lib/apt/lists/*

# Harnesses pinned to the versions measured on the host.
RUN npm install -g --no-audit --no-fund \
      @earendil-works/pi-coding-agent@0.86.0 \
      @deepseek-ai/dsh@0.1.5-rc.2

# Harness configuration (endpoints repointed at the host gateway alias "llama").
COPY config/pi  /root/.pi/agent
COPY config/dsh /root/.dsh

# The seed repository, node_modules already vendored.
COPY seed /opt/seed
COPY PROMPT.txt /opt/PROMPT.txt

# No dependency installation is possible during a run.
ENV npm_config_offline=true \
    npm_config_audit=false \
    npm_config_fund=false \
    CI=true

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
