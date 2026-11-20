#!/bin/sh
# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
#
# Builds the libyang image once and runs validate.py in this directory.
set -e
cd "$(dirname "$0")"
docker build -q -t on-change-case . >/dev/null
docker run --rm -v "$PWD":/case:ro on-change-case
