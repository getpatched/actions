# syntax=docker/dockerfile:1.7
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG APT_SOURCES_B64
ARG SMOKE_DEPENDENCIES
ARG CANDIDATE_REQUESTS
ARG BASELINE_REQUESTS
ENV DEBIAN_FRONTEND=noninteractive
RUN --mount=type=secret,id=runner-ca,target=/tmp/runner-ca,mode=0444,required=true \
    --mount=type=bind,source=.,target=/candidate,readonly \
    set -eu; \
    rm -f /etc/apt/sources.list.d/* /etc/apt/apt.conf.d/docker-clean; \
    printf '%s' "$APT_SOURCES_B64" | base64 -d > /etc/apt/sources.list; \
    apt-get -o Acquire::https::CAInfo=/tmp/runner-ca update; \
    if [ -n "$BASELINE_REQUESTS" ]; then \
      apt-get -o Acquire::https::CAInfo=/tmp/runner-ca --download-only -y \
        install --no-install-recommends $BASELINE_REQUESTS $SMOKE_DEPENDENCIES; \
    fi; \
    printf '%s\n' 'deb [signed-by=/candidate/archive-keyring.gpg] file:/candidate ./' \
      > /etc/apt/sources.list.d/getpatched-test.list; \
    apt-get -o Dir::Etc::sourcelist=sources.list.d/getpatched-test.list \
      -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=false \
      -o Acquire::AllowInsecureRepositories=false update; \
    apt-get -o Acquire::https::CAInfo=/tmp/runner-ca --download-only -y \
      install --no-install-recommends $CANDIDATE_REQUESTS $SMOKE_DEPENDENCIES; \
    rm -f /etc/apt/sources.list.d/getpatched-test.list /var/lib/apt/lists/_candidate_*
