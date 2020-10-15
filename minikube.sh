#!/usr/bin/env bash

set -o verbose
set -o errexit

function minikube::install {
    curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
    sudo install minikube-linux-amd64 /usr/local/bin/minikube
}

function minikube::start {
    minikube config set WantUpdateNotification false
    minikube start --kubernetes-version=v1.16.15 --wait=all --wait-timeout=5m
    kubectl config use-context minikube
    kubectl label node minikube node.kubernetes.io/instance-type=minikube
}

case "${1:-}" in
    install)
        minikube::install
        ;;
    start)
        minikube::start
        ;;
esac
