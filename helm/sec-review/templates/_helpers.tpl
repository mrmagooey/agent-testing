{{/*
Expand the name of the chart.
*/}}
{{- define "sec-review.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully qualified app name. Truncated at 63 chars for K8s DNS label compliance.
*/}}
{{- define "sec-review.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Chart name + version used on the helm.sh/chart label.
*/}}
{{- define "sec-review.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to every resource owned by this release.
*/}}
{{- define "sec-review.labels" -}}
helm.sh/chart: {{ include "sec-review.chart" . }}
app.kubernetes.io/name: {{ include "sec-review.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels — stable across upgrades; never include version.
*/}}
{{- define "sec-review.coordinator.selectorLabels" -}}
app.kubernetes.io/name: {{ include "sec-review.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: coordinator
app: sec-review-coordinator
{{- end }}

{{/*
Image references — "<repo>:<tag>" for each role.
*/}}
{{- define "sec-review.coordinatorImage" -}}
{{- printf "%s:%s" .Values.image.coordinator.repository .Values.image.coordinator.tag }}
{{- end }}

{{- define "sec-review.workerImage" -}}
{{- printf "%s:%s" .Values.image.worker.repository .Values.image.worker.tag }}
{{- end }}

{{- define "sec-review.datasetBuilderImage" -}}
{{- printf "%s:%s" .Values.image.datasetBuilder.repository .Values.image.datasetBuilder.tag }}
{{- end }}

{{/*
ServiceAccount name for the coordinator.
*/}}
{{- define "sec-review.serviceAccountName" -}}
{{- default (printf "%s-coordinator" (include "sec-review.fullname" .)) .Values.serviceAccount.name }}
{{- end }}

{{/*
Gatekeeper allowed images — falls back to the current worker + dataset-builder
references when the caller hasn't pinned an explicit list.
*/}}
{{- define "sec-review.gatekeeperAllowedImages" -}}
{{- if .Values.gatekeeper.allowedImages }}
{{- toYaml .Values.gatekeeper.allowedImages }}
{{- else }}
- {{ include "sec-review.workerImage" . | quote }}
- {{ include "sec-review.datasetBuilderImage" . | quote }}
{{- end }}
{{- end }}
