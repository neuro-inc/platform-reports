#!/usr/bin/env python

import subprocess

from yarl import URL


IMAGE_REPO = "neuro-docker-local-public.jfrog.io"
# IMAGE_REPO = "ghcr.io/neuro-inc"


def push(image: str) -> None:
    image_src = URL(image)

    if "/" in image:
        image_dst = URL(IMAGE_REPO) / image_src.parts[-1]
    else:
        image_dst = URL(IMAGE_REPO) / image

    subprocess.run(["docker", "pull", str(image_src)], check=True)
    subprocess.run(["docker", "tag", str(image_src), str(image_dst)], check=True)
    subprocess.run(["docker", "push", str(image_dst)], check=True)


images = [
    # # platform-monitoring
    # "bitnami/fluentd:1.10.4-debian-10-r2",
    # "fluent/fluent-bit:1.3.7",
    # "minio/minio:RELEASE.2021-08-25T00-41-18Z",
    # # platform-reports
    # "nvidia/dcgm-exporter:1.7.2",
    # "quay.io/coreos/kube-state-metrics:v1.9.6",
    # "quay.io/prometheus/node-exporter:v1.0.0",
    # "quay.io/coreos/prometheus-operator:v0.38.2",
    # "quay.io/coreos/prometheus-config-reloader:v0.38.2",
    # "quay.io/coreos/configmap-reload:v0.0.1",
    # "squareup/ghostunnel:v1.5.2",
    # "jettech/kube-webhook-certgen:v1.2.1",
    # "quay.io/prometheus/prometheus:v2.18.1",
    # "quay.io/thanos/thanos:v0.14.0",
    # "grafana/grafana:7.2.1",
    # "busybox:latest",
    # "kiwigrid/k8s-sidecar:1.1.0",
    # # platform-operator
    # "nvidia/k8s-device-plugin:1.0.0-beta6",
    # "gcr.io/google_containers/pause:3.0",
    # "alpine:latest",
    # "neuro-docker-local-public.jfrog.io/crictl:1.22.0",
    # "bitnami/kubectl:1.16",
    # "registry:2.7.1",
    # "hashicorp/consul:1.9.2",
    "consul:1.5.3",
    # "traefik:1.7.20-alpine",
]

for image in images:
    push(image)
