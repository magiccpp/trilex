FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      postfix opendkim opendkim-tools ca-certificates rsyslog \
 && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Deliberately NOT published in docker-compose: this listener is reachable only
# from the private compose network, so the host never exposes SMTP publicly.
EXPOSE 25
ENTRYPOINT ["/entrypoint.sh"]
