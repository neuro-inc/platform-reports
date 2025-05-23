#!/usr/bin/env bash

set -o verbose
set -o errexit

function minikube::install {
    sudo apt-get update
    sudo apt-get install -y conntrack
    curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
    sudo install minikube-linux-amd64 /usr/local/bin/minikube
}

function minikube::start {
    minikube config set WantUpdateNotification false
    minikube start --vm-driver=docker --wait=all --wait-timeout=5m
    kubectl config use-context minikube
    kubectl label nodes --all \
        topology.kubernetes.io/zone=minikube-zone \
        node.kubernetes.io/instance-type=minikube \
        platform.neuromation.io/nodepool=minikube-node-pool
}

case "${1:-}" in
    install)
        minikube::install
        ;;
    start)
        minikube::start
        ;;
esac
