# syntax=docker/dockerfile:1.7
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG APT_SOURCES_B64
ARG SMOKE_DEPENDENCIES
ENV DEBIAN_FRONTEND=noninteractive
RUN --mount=type=secret,id=runner-ca,target=/tmp/runner-ca,required=true \
    --mount=type=bind,source=.,target=/candidate,readonly \
    set -eu; \
    rm -f /etc/apt/sources.list.d/* /etc/apt/apt.conf.d/docker-clean; \
    printf '%s' "$APT_SOURCES_B64" | base64 -d > /etc/apt/sources.list; \
    apt-get -o Acquire::https::CAInfo=/tmp/runner-ca update; \
    apt-get -o Acquire::https::CAInfo=/tmp/runner-ca --download-only -y \
      install --no-install-recommends /candidate/*.deb $SMOKE_DEPENDENCIES
