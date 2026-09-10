{{- define "platform-base.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: ohdp-platform
{{- end -}}

{{/*
Resolve every internally-generated password once. Reads the canonical Secret
from the live cluster first (so values never rotate on upgrade) and falls back
to a fresh randAlphaNum. Returns a dict of base64-encoded values, keyed by the
name used in the Secret `data:` block.

  {{- $pw := include "platform-base.passwords" . | fromYaml }}
  {{- $pw.postgres }}   # base64 of the postgres superuser password

During `helm template` with no cluster the lookups return nothing and every
value is freshly random — fine for validation, never written anywhere.
*/}}
{{- define "platform-base.passwords" -}}
{{- $len := int .Values.generatedSecrets.passwordLength -}}
{{- $pg := (lookup "v1" "Secret" "infra" "postgres-secret").data | default dict -}}
{{- $fernet := (lookup "v1" "Secret" "meta" "openmetadata-fernet-secret").data | default dict -}}
{{- $cube := (lookup "v1" "Secret" "data" "cube-secret").data | default dict -}}
{{- $oauth2 := (lookup "v1" "Secret" "data" "oauth2-proxy-secret").data | default dict -}}
postgres: {{ (index $pg "postgres-password") | default (randAlphaNum $len | b64enc) }}
dagster: {{ (index $pg "dagster-password") | default (randAlphaNum $len | b64enc) }}
superset: {{ (index $pg "superset-password") | default (randAlphaNum $len | b64enc) }}
openmetadata: {{ (index $pg "openmetadata-password") | default (randAlphaNum $len | b64enc) }}
app: {{ (index $pg "app-password") | default (randAlphaNum $len | b64enc) }}
polaris: {{ (index $pg "polaris-password") | default (randAlphaNum $len | b64enc) }}
fernet: {{ (index $fernet "fernetKey") | default ((randAlphaNum 32 | b64enc | replace "+" "-" | replace "/" "_") | b64enc) }}
cube: {{ (index $cube "OHDP_CUBE_API_SECRET") | default (randAlphaNum $len | b64enc) }}
{{/* oauth2-proxy cookie secret — must decode to exactly 32 bytes. */}}
oauth2Cookie: {{ (index $oauth2 "cookie-secret") | default (randAlphaNum 32 | b64enc) }}
{{- end -}}
