FROM golang:1.24.8-bookworm@sha256:4ed690d6649d63c312b99a6120025ec79ce3b542968a37da53d6236c7c61a848 AS builder

# Immutable upstream security release; Go module downloads are verified by sum.golang.org.
ENV CGO_ENABLED=0 GOTOOLCHAIN=local
WORKDIR /build
RUN go mod download -json github.com/minio/minio@9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a > /tmp/module.json \
    && module_dir="$(sed -n 's/.*"Dir": "\([^"]*\)".*/\1/p' /tmp/module.json)" \
    && test -n "$module_dir" \
    && cd "$module_dir" \
    && go build -mod=readonly -trimpath -buildvcs=false -o /out/minio . \
    && mkdir -p /out/licenses \
    && cp LICENSE README.md /out/licenses/

FROM alpine:3.22.2@sha256:4b7ce07002c69e8f3d704a9c5d6fd3053be500b7f1c69fc0d80990c2ad8dd412
LABEL org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.revision="9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
COPY --from=builder /out/minio /usr/local/bin/minio
COPY --from=builder /out/licenses /usr/share/licenses/minio
COPY --from=builder /tmp/module.json /usr/share/licenses/minio/source-info.json
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
RUN mkdir /data && chown 10001:10001 /data
USER 10001:10001
ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]
