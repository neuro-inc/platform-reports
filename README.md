# platform-reports

## S3/SeaweedFS/MinIO Configuration

This service supports S3-compatible storage backends:
- **SeaweedFS** (recommended for production)
- **MinIO** (development, test)

### Choosing Backend
- Configure the backend in `values.yaml` under the `s3` section.

### Example `values.yaml`
```yaml
# MinIO config example (commented):
# s3:
#   region: "us-east-1"
#   endpoint: "http://minio.platform:9000"
#   bucket: "platform-reports"
#   accessKeyId:
#     valueFrom:
#       secretKeyRef:
#         name: minio-secret
#         key: access_key_id
#   secretAccessKey:
#     valueFrom:
#       secretKeyRef:
#         name: minio-secret
#         key: secret_access_key

# SeaweedFS config (default):
s3:
  region: "us-east-1"
  endpoint: "http://seaweedfs-s3:9000"
  bucket: "platform-reports"
  accessKeyId:
    valueFrom:
      secretKeyRef:
        name: seaweedfs-s3-secret
        key: admin_access_key_id
  secretAccessKey:
    valueFrom:
      secretKeyRef:
        name: seaweedfs-s3-secret
        key: admin_secret_access_key
  # For readonly access, use read_access_key_id/read_secret_access_key.
```

### Kubernetes Secret Example
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: seaweedfs-s3-secret
  namespace: <your-namespace>
type: Opaque
data:
  admin_access_key_id: <base64-encoded>
  admin_secret_access_key: <base64-encoded>
  read_access_key_id: <base64-encoded>
  read_secret_access_key: <base64-encoded>
```

### Testing the Connection
- Use `kubectl port-forward` to expose SeaweedFS S3:
  ```shell
  kubectl port-forward svc/seaweedfs-s3 9000:9000 -n <namespace>
  ```
- Use `aws-cli` or `mc` to test connectivity:
  ```shell
  AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... aws --endpoint-url http://localhost:9000 s3 ls
  # or
  mc alias set seaweed http://localhost:9000 <admin_access_key_id> <admin_secret_access_key>
  mc ls seaweed
  ```

### Notes
- Change provider by editing the `s3` section in `values.yaml`.
- Set readonly deployments via readonly keys from the secret.
- See Helm chart templates for the exact environment variable mappings.
