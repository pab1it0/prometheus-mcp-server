# Prometheus MCP Server Helm Chart

A Helm chart for deploying the [Prometheus MCP Server](https://github.com/pab1it0/prometheus-mcp-server) to Kubernetes.

## Prerequisites

- Kubernetes >= 1.19
- Helm >= 3.0
- A Prometheus instance accessible from the cluster

## Installation

### From OCI Registry

```bash
helm install prometheus-mcp-server \
  oci://ghcr.io/pab1it0/charts/prometheus-mcp-server \
  --set prometheus.url=http://prometheus:9090
```

### From Source

```bash
git clone https://github.com/pab1it0/prometheus-mcp-server.git
cd prometheus-mcp-server

helm install prometheus-mcp-server ./charts/prometheus-mcp-server \
  --set prometheus.url=http://prometheus:9090
```

## Configuration

### Prometheus Connection

| Parameter | Description | Default |
|-----------|-------------|---------|
| `prometheus.url` | Prometheus server URL (**required**) | `http://prometheus:9090` |
| `prometheus.sslVerify` | Enable SSL verification | `"true"` |
| `prometheus.disableLinks` | Disable Prometheus UI links in responses (saves context tokens) | `"false"` |
| `prometheus.requestTimeout` | Request timeout in seconds | `"30"` |
| `prometheus.customHeaders` | Custom headers as JSON string | `""` |

### Authentication

| Parameter | Description | Default |
|-----------|-------------|---------|
| `auth.existingSecret` | Use an existing Kubernetes Secret for credentials | `""` |
| `auth.username` | Basic auth username | `""` |
| `auth.password` | Basic auth password | `""` |
| `auth.token` | Bearer token | `""` |
| `auth.orgId` | Organization ID for multi-tenant setups (X-Scope-OrgID header) | `""` |

When `auth.existingSecret` is set, the chart expects the Secret to contain the keys: `PROMETHEUS_USERNAME`, `PROMETHEUS_PASSWORD`, `PROMETHEUS_TOKEN`, and/or `ORG_ID`.

### MCP Server

| Parameter | Description | Default |
|-----------|-------------|---------|
| `mcp.transport` | Transport mode: `http`, `sse`, or `stdio` | `"http"` |
| `mcp.bindHost` | Bind address | `"0.0.0.0"` |
| `mcp.toolPrefix` | Prefix for all MCP tool names (e.g., `staging`) | `""` |

### Deployment

| Parameter | Description | Default |
|-----------|-------------|---------|
| `replicaCount` | Number of replicas | `1` |
| `image.repository` | Container image repository | `ghcr.io/pab1it0/prometheus-mcp-server` |
| `image.tag` | Container image tag (defaults to `appVersion`) | `""` |
| `image.pullPolicy` | Image pull policy | `IfNotPresent` |
| `imagePullSecrets` | Image pull secrets | `[]` |
| `nameOverride` | Override chart name | `""` |
| `fullnameOverride` | Override full release name | `""` |
| `containerPort` | Container port | `8080` |
| `resources` | CPU/memory resource requests and limits | `{}` |
| `nodeSelector` | Node selector labels | `{}` |
| `tolerations` | Pod tolerations | `[]` |
| `affinity` | Pod affinity rules | `{}` |
| `podAnnotations` | Additional pod annotations | `{}` |
| `podLabels` | Additional pod labels | `{}` |
| `extraEnv` | Additional environment variables | `[]` |
| `extraVolumes` | Additional volumes | `[]` |
| `extraVolumeMounts` | Additional volume mounts | `[]` |

### Service

| Parameter | Description | Default |
|-----------|-------------|---------|
| `service.type` | Service type | `ClusterIP` |
| `service.port` | Service port | `8080` |
| `service.annotations` | Service annotations | `{}` |

### Ingress

| Parameter | Description | Default |
|-----------|-------------|---------|
| `ingress.enabled` | Enable ingress | `false` |
| `ingress.ingressClassName` | Ingress class name | `""` |
| `ingress.annotations` | Ingress annotations | `{}` |
| `ingress.hosts` | Ingress host rules | see `values.yaml` |
| `ingress.tls` | Ingress TLS configuration | `[]` |

### ServiceMonitor

For Prometheus Operator integration:

| Parameter | Description | Default |
|-----------|-------------|---------|
| `serviceMonitor.enabled` | Create a ServiceMonitor resource | `false` |
| `serviceMonitor.labels` | Additional labels for the ServiceMonitor | `{}` |
| `serviceMonitor.namespace` | Namespace for the ServiceMonitor | `""` |
| `serviceMonitor.interval` | Scrape interval | `""` |
| `serviceMonitor.scrapeTimeout` | Scrape timeout | `""` |

### ServiceAccount

| Parameter | Description | Default |
|-----------|-------------|---------|
| `serviceAccount.create` | Create a service account | `true` |
| `serviceAccount.name` | Service account name | `""` |
| `serviceAccount.annotations` | Service account annotations | `{}` |
| `serviceAccount.automountServiceAccountToken` | Automount the service account token | `false` |

### Security Context

The chart ships with secure defaults:

| Parameter | Description | Default |
|-----------|-------------|---------|
| `podSecurityContext.runAsNonRoot` | Run as non-root user | `true` |
| `podSecurityContext.runAsUser` | User ID | `1000` |
| `podSecurityContext.runAsGroup` | Group ID | `1000` |
| `containerSecurityContext.readOnlyRootFilesystem` | Read-only root filesystem | `true` |
| `containerSecurityContext.allowPrivilegeEscalation` | Disallow privilege escalation | `false` |

## Examples

### Minimal Installation

```bash
helm install prometheus-mcp-server \
  oci://ghcr.io/pab1it0/charts/prometheus-mcp-server \
  --set prometheus.url=http://prometheus:9090
```

### With Basic Auth

```bash
helm install prometheus-mcp-server \
  oci://ghcr.io/pab1it0/charts/prometheus-mcp-server \
  --set prometheus.url=https://prometheus.example.com \
  --set auth.username=admin \
  --set auth.password=secret
```

### With Existing Secret

```bash
# Create the secret first
kubectl create secret generic prometheus-auth \
  --from-literal=PROMETHEUS_USERNAME=admin \
  --from-literal=PROMETHEUS_PASSWORD=secret

helm install prometheus-mcp-server \
  oci://ghcr.io/pab1it0/charts/prometheus-mcp-server \
  --set prometheus.url=https://prometheus.example.com \
  --set auth.existingSecret=prometheus-auth
```

### With Ingress and TLS

```yaml
# values-production.yaml
prometheus:
  url: "https://prometheus.internal:9090"
  disableLinks: "true"

auth:
  existingSecret: prometheus-credentials

mcp:
  transport: "http"
  toolPrefix: "prod"

ingress:
  enabled: true
  ingressClassName: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt
  hosts:
    - host: mcp.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: mcp-tls
      hosts:
        - mcp.example.com

resources:
  requests:
    cpu: 100m
    memory: 128Mi
  limits:
    cpu: 200m
    memory: 256Mi

serviceMonitor:
  enabled: true
  labels:
    release: prometheus
```

```bash
helm install prometheus-mcp-server \
  oci://ghcr.io/pab1it0/charts/prometheus-mcp-server \
  -f values-production.yaml
```

## Accessing the Server

After installation, access the MCP endpoint via port-forward:

```bash
kubectl port-forward svc/prometheus-mcp-server 8080:8080
```

The MCP endpoint will be available at `http://127.0.0.1:8080/sse`.
