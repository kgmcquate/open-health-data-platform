# Vendored chart — do not hand-edit

This is `console/helm` copied verbatim from **apache/polaris-tools**. Upstream
publishes the console chart to no Helm repo and its image to no registry
(ADR-0010), so both are vendored:

- chart → this directory
- image → built by `.github/workflows/build-polaris-console.yml` and pushed to
  `ghcr.io/kgmcquate/polaris-console`

Pinned ref: `da984987b91ffb421502059dea422b9331186e46` (chart `1.6.0-SNAPSHOT`).

Our configuration lives in `platform/helm/values/polaris-console.yaml`, never in
this tree. To take a newer upstream:

```
platform/scripts/vendor-polaris-console.sh <polaris-tools-ref>
```

then bump `POLARIS_TOOLS_REF` in `build-polaris-console.yml` and `image.tag` in
`values/polaris-console.yaml` to the same ref, and re-run that workflow.
