# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT

# libyang 6.4.13 and its Python binding 5.1.0, the versions of the consumer
# in the YANG-Push message broker chain. Debian packages an older libyang,
# so it is built from its release tag.
FROM python:3.14-slim-trixie

ARG LIBYANG_VERSION=6.4.13
ARG LIBYANG_SHA256=bf78a437225027ce9e20d2c449b7853204d7fd450bb00fb01e83339145561a86

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential cmake libpcre2-dev \
 && rm -rf /var/lib/apt/lists/*

ADD --checksum=sha256:${LIBYANG_SHA256} \
    https://github.com/CESNET/libyang/archive/refs/tags/v${LIBYANG_VERSION}.tar.gz /tmp/libyang.tar.gz

COPY requirements.txt /tmp/requirements.txt

RUN mkdir /src && tar -xzf /tmp/libyang.tar.gz -C /src --strip-components=1 \
 && cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local \
      -DENABLE_TESTS=OFF -DENABLE_TOOLS=OFF \
 && cmake --build /build -j"$(nproc)" \
 && cmake --install /build \
 && ldconfig \
 && pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /case
ENTRYPOINT ["python3", "-I", "validate.py"]
