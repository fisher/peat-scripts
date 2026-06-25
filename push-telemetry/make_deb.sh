#!/bin/sh

dpkg-buildpackage -us -uc -b
rm -rf debian/push-telemetry

