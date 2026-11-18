# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT

# YANG message broker consumer for ClickHouse.
# Debian 13 packages libyang 3.12. The current Python binding needs libyang
# with SO version 5.11.9 or later, so libyang is built from its release tag.
FROM python:3.14-slim-trixie AS libyang

ARG LIBYANG_VERSION=6.4.13
ARG LIBYANG_SHA256=bf78a437225027ce9e20d2c449b7853204d7fd450bb00fb01e83339145561a86

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential cmake libpcre2-dev \
 && rm -rf /var/lib/apt/lists/*

ADD --checksum=sha256:${LIBYANG_SHA256} \
    https://github.com/CESNET/libyang/archive/refs/tags/v${LIBYANG_VERSION}.tar.gz /tmp/libyang.tar.gz

RUN mkdir /src && tar -xzf /tmp/libyang.tar.gz -C /src --strip-components=1 \
 && cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local \
      -DENABLE_TESTS=OFF -DENABLE_TOOLS=OFF \
 && cmake --build /build -j"$(nproc)" \
 && cmake --install /build

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r /app/requirements.txt

FROM python:3.14-slim-trixie

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpcre2-8-0 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=libyang /usr/local/lib/libyang.so* /usr/local/lib/
# libyang 6 loads its internal modules from here at context creation.
COPY --from=libyang /usr/local/share/yang /usr/local/share/yang
COPY --from=libyang /install /usr/local
RUN ldconfig

COPY consumer.py report.py /app/
WORKDIR /app
ENTRYPOINT ["python", "-u", "/app/consumer.py"]
