# Artifact storage: HeadBucket permission and source maintenance

Accepted for CP3, 2026-09-11. Production upload composition remains local until Task 3.

The application is a server-side service identity. S3 maps both HeadBucket and
ListObjectsV2 to `s3:ListBucket`; there is no narrower HeadBucket IAM action.
The policy therefore permits listing the single `recruitmatch-artifacts` bucket,
and Put/Get/Delete only under its `tenants/*` prefix. HeadObject maps to GetObject.
Health performs authenticated HeadBucket only. Tenant authorization remains the
application's responsibility; application credentials never go to end users.
Root credentials are allowlisted only into the server, one-shot initializer and
one-shot synthetic test setup (enabled only by the test profile).
On this MinIO revision, ListBuckets also returns the permitted bucket via a
filtered fallback to bucket-scoped ListBucket; tests assert that exact returned
scope with a second private synthetic bucket present, alongside denial of access
to that bucket. See the pinned
[ListBuckets handler](https://github.com/minio/minio/blob/9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a/cmd/bucket-handlers.go#L283-L381).

References: [AWS S3 IAM mapping](https://docs.aws.amazon.com/AmazonS3/latest/userguide/security_iam_service-with-iam.html)
and [MinIO checksum discussion](https://github.com/minio/minio/discussions/20400).
The tests prove wrong SHA-256 rejection by this backend; ETags and custom metadata
are not integrity evidence. The typed inspection port returns checked size/digest
or stable errors for later reconciliation. Orphan enumeration is deferred to Task 4.

The server builds upstream commit `9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a`,
the [October 2025 security release](https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z)
fixing session-policy privilege escalation. [Upstream issue 21647](https://github.com/minio/minio/issues/21647)
confirms a corresponding downloadable container is not expected. Go module
checksums verify downloaded sources; the Dockerfile pins toolchain/runtime versions,
uses static compilation and a non-root runtime, and retains the upstream license
and README under `/usr/share/licenses/minio`. Source remains available at the
revision link and via Go's module proxy. No upstream source is modified.

[Upstream MinIO](https://github.com/minio/minio) is archived and no longer maintained.
This build is a bounded demo dependency, not a promise of ongoing security updates.
Maintainers own future source/base/client updates, repeat checksum/IAM contracts,
and must assess a maintained alternative before production use. CP6 owns the full
image/security scan; pinning and inclusion of one security fix do not establish
that this image is free of other vulnerabilities. Do not substitute an older
pre-fix image or use `latest`. Demo root/app passwords must be replaced outside
local demonstrations. MinIO publishes no host ports and persists only its data
volume; initializer configuration is ephemeral.

## Client registry correction (2026-09-14)

The CP4 PR's [integration build failed](https://github.com/xder393/recruitmatch-copilot/actions/runs/34802994122/job/103849472309)
before tests started: Docker Hub denied resolution of the pinned `minio/mc`
manifest (`insufficient_scope`). This was also reproduced by a fresh registry
metadata request; a previously cached local image had hidden the availability
problem. The error alone does not establish why Docker Hub denied access.

All three client consumers (the test-image build stage, `minio-init`, and
`minio-test-setup`) now use `quay.io/minio/mc`. The upstream release's
[publication script](https://github.com/minio/mc/blob/RELEASE.2025-08-13T08-35-41Z/docker-buildx.sh)
publishes to both Docker Hub and this Quay repository. A live Quay manifest query
confirmed the **same** release `RELEASE.2025-08-13T08-35-41Z`, multi-platform digest
`sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727`,
and Linux amd64/arm64/ppc64le entries. This changes the registry only: no client
upgrade, digest removal, third-party mirror, credentials, IAM changes, or server
rollback. The maintenance and security limitations above still apply.

The existing CI build plus two initializer runs and real MinIO IAM tests are the
regression gate. Unit tests remain offline: they must not query a public registry.
Local build cache reuse is not proof of a clean GitHub runner build; report the
remote CI result separately.

Local verification on 2026-09-14 used `.env.example` and the isolated Compose
project `recruitmatch-ci-fix-20260914`: explicit pulls for both client services,
`build bootstrap test-integration minio`, two successful bootstrap runs (30 then
0 seeded templates), two successful initializer runs, 286 unit tests and 503 real
PostgreSQL/MinIO/retrieval tests. Lock checking, Ruff, mypy and the Fake AI golden
diff also passed. Build layers were reused; test data volumes were newly created.
No real LLM calls or changes to existing application data were involved.
