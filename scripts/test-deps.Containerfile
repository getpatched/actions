ARG BUILDER_IMAGE
FROM ${BUILDER_IMAGE}
ARG TEST_DEPENDENCIES
RUN apt-get update && apt-get install -y --no-install-recommends $TEST_DEPENDENCIES \
    && rm -rf /var/lib/apt/lists/*
