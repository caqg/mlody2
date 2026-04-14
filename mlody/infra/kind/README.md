# Kind Cluster Provisioner

Sets up a local [kind](https://kind.sigs.k8s.io/) (Kubernetes IN Docker) cluster
with a connected local Docker registry. Once provisioned, you can push images to
`localhost:5001` and pull them inside the cluster without any extra
configuration.

## Prerequisites

- [Docker](https://docs.docker.com/engine/install/)
- [kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)

## Provisioning the cluster

```sh
bazel run //mlody/infra/kind:o-mlody-cluster
```

This runs six steps in order:

1. Start a local Docker registry container (`kind-registry` on port `5001`)
2. Create a kind cluster named `mlody` configured to mirror `localhost:5001`
3. Configure containerd on each cluster node to use the registry
4. Connect the registry container to the `kind` Docker network
5. Apply a `local-registry-hosting` ConfigMap so tooling can discover the
   registry automatically
6. Apply CPU/memory limits to each node (skipped unless `--max-cpus` or
   `--max-memory` is set)

The command is **idempotent** — running it again skips steps that are already
complete.

### Useful flags

| Flag              | Default                | Description                                     |
| ----------------- | ---------------------- | ----------------------------------------------- |
| `--cluster-name`  | `mlody`                | Name of the kind cluster                        |
| `--registry-name` | `kind-registry`        | Name of the registry Docker container           |
| `--registry-port` | `5001`                 | Host port for the registry                      |
| `--kubeconfig`    | _(default kubeconfig)_ | Path to a kubeconfig file override              |
| `--save-config`   | _(not saved)_          | Save the generated kind YAML to a file          |
| `--force`         | `false`                | Tear down and recreate the cluster and registry |
| `--dry-run`       | `false`                | Print commands without executing them           |
| `--verbose`       | `false`                | Print each command before running it            |
| `--quiet`         | `false`                | Suppress non-essential output                   |
| `--max-cpus`      | _(no limit)_           | CPU limit per node, e.g. `2` or `0.5`           |
| `--max-memory`    | _(no limit)_           | Memory limit per node, e.g. `4g` or `512m`      |

Pass flags after a `--` separator:

```sh
bazel run //mlody/infra/kind:o-mlody-cluster -- --dry-run
bazel run //mlody/infra/kind:o-mlody-cluster -- --force --verbose
```

## Smoketest: build an image and run it in the cluster

`hello-world.sh` does the full cycle in one shot: builds a minimal Python HTTP
server image in a temporary directory (cleaned up on exit), pushes it to the
local registry, deploys it to the cluster, and verifies the response.

```sh
./hello-world.sh
```

If you used non-default values when provisioning, pass them here too:

```sh
./hello-world.sh --registry-port 5002 --cluster-name my-cluster
```

A successful run ends with:

```
OK: Hello from kind!
```

## Checking cluster state

```sh
# List all nodes
kubectl get nodes

# List all pods across all namespaces
kubectl get pods -A

# Describe a specific pod (useful when a pod won't start)
kubectl describe pod hello-world

# Check pod logs
kubectl logs hello-world

# Check the local-registry ConfigMap applied in step 5
kubectl get configmap local-registry-hosting -n kube-public -o yaml

# List all running Docker containers (includes the registry and kind nodes)
docker ps
```

## Tearing down

```sh
# Delete the cluster
kind delete cluster --name mlody

# Stop and remove the registry
docker rm -f kind-registry
```

Or reprovision from scratch:

```sh
bazel run //mlody/infra/kind:o-mlody-cluster -- --force
```
