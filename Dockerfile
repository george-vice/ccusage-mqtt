FROM node:22-alpine

RUN apk add --no-cache python3 py3-pip

# Pin ccusage version. Bump deliberately.
RUN npm install -g ccusage@18.0.11

WORKDIR /app
COPY pyproject.toml ./
COPY ccusage_mqtt ./ccusage_mqtt

RUN pip install --break-system-packages --no-cache-dir .

# Run as non-root for defense in depth — uid is arbitrary, doesn't need to
# match the host's claude user since the mount is read-only.
RUN adduser -D -u 10001 ccusage
USER ccusage

ENTRYPOINT ["python3", "-m", "ccusage_mqtt"]
