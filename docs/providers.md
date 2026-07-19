# Model and embedding providers

## Default offline route

`offline/extractive-v1` is the complete no-key baseline. It ranks canonical evidence, selects
representative facts, preserves citations, fills unknown fields explicitly, and never opens a
network connection. Local hashing embeddings populate the baseline Chroma collection.

The offline route is deterministic and useful for continuity, but it is not equivalent to semantic
vision reasoning by a calibrated multimodal model. That distinction appears in output confidence
and validation records.

## Optional generation adapters

| Provider | SDK boundary | Capabilities represented by the adapter |
|---|---|---|
| OpenAI | Responses API | Text and selected managed images/page renders |
| Anthropic | Messages API | Text and selected managed images/page renders |
| Google | `google-genai` | Text and selected managed images/page renders |
| xAI | Official xAI SDK | Text and selected managed images/page renders |

These rows describe the adapters implemented here, not every feature a provider platform may
offer. The adapters do not upload native PDFs and do not use provider document-search or file-search
attachments. PDF pages and embedded images are first rendered or cropped into project-managed image
artifacts. Only the managed visual artifacts selected by retrieval are sent to an image-capable
generation route, after capability checks and explicit cloud-upload consent.

Adapter availability has four states: installed and configured, installed but unconfigured,
uninstalled, or disabled by offline/network policy. Diagnostics report only these states and key
names; they never expose values.

## Use a cloud provider

Cloud use has two separate gates. First, start Handoff Forge with network access. Second, consent
to the upload for that generation. Enabling the first gate never grants the second one.

Install all official adapters from a source checkout:

```console
python -m pip install -e '.[providers]'
handoff-forge --allow-network doctor
```

Or install them with a locally built wheel:

```console
python -m pip install 'handoff-forge[providers] @ file:///ABSOLUTE/PATH/handoff_forge-0.3.0-py3-none-any.whl'
handoff-forge --allow-network doctor
```

Set only the credential name for the provider you plan to use:

| Provider | Credential variable |
|---|---|
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| Google | `GEMINI_API_KEY` or `GOOGLE_API_KEY` |
| xAI | `XAI_API_KEY` |

Then opt in for one CLI generation. Replace the uppercase values with a project name, provider,
and exact model identifier that your account can use:

```console
handoff-forge --allow-network generate \
  --project PROJECT \
  --provider PROVIDER \
  --model MODEL_ID \
  --allow-cloud-upload
```

In the workbench, start it with `handoff-forge --allow-network ui`, choose the provider, then turn
on cloud-upload consent for the current handoff. That consent applies to the requested generation;
it does not replace the network gate. Visual-file upload remains a separate opt-in.

### Docker

The default image stays offline and omits cloud SDKs. Build an explicit provider image, then pass
through only the credential variable you need. The name-only `--env` form reads its value from the
host environment without putting the value in this command:

```console
docker build \
  --build-arg HANDOFF_FORGE_INSTALL_PROVIDERS=true \
  --tag handoff-forge:providers .

docker run --rm \
  --publish 127.0.0.1:8517:8517 \
  --read-only \
  --tmpfs /tmp:size=256m,mode=1777 \
  --volume handoff-forge-data:/data \
  --env HANDOFF_FORGE_OFFLINE=false \
  --env HANDOFF_FORGE_ALLOW_NETWORK=true \
  --env OPENAI_API_KEY \
  handoff-forge:providers
```

Open `http://127.0.0.1:8517` and still grant consent on the individual generation. Substitute
`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, or `XAI_API_KEY` for the final `--env`
name when using a different adapter. Do not pass a credential that the selected provider does not
need.

## Per-section routing

A route records provider, exact model identifier, temperature, maximum output tokens, cloud-upload
consent, and whether selected visual files may be included. Visual-file inclusion defaults to off.
The CLI exposes one explicit run-wide switch; the UI supports a default plus a separate override for
every section and the post-chat inventory. A visual block's extracted text remains available when
file inclusion is off, but its managed image bytes are not read or sent.

The adapter declares whether its implemented API path accepts image input. Enabling visual-file
inclusion is the operator's separate attestation that the exact selected model/version also accepts
images; Handoff Forge does not pretend to maintain a live model capability registry. Preflight
rejects opted-in image bytes when the adapter lacks image support, as well as unsupported MIME or
size limits, a missing SDK, a missing credential, or network-disabled execution before a paid call.
Provider-side model availability and entitlement still require separately labeled live calibration.

Completed sections retain provider, exact model identifier, consent, and visual-file choice in the
sanitized route manifest. The recorded operator attestation is not proof that the provider accepted
that model/version. Aliases may be convenient selections but are not represented as reproducible
model-version proof.

## Text embedding indexes

Both implemented indexes embed text. The offline index uses deterministic local hashing. The
opt-in Voyage index uses the text model `voyage-3.5`; it does not upload image bytes or produce
multimodal image embeddings. Its preflight disables truncation and rejects an over-limit text batch
instead of silently dropping evidence. Each embedding fingerprint receives a separate Chroma
collection, and the canonical store plus local text index remain usable when Voyage is unavailable.

Visual blocks can still be found by text search because their node text carries bounded local
context: Markdown alt text or filename-derived description, and for PDFs same-page native text,
tables, and optional OCR text. This is contextual text retrieval, not pixel-semantic chart or image
embedding. After retrieval, an image-capable generation provider can inspect the selected managed
artifact itself.

## Adding a provider

Implement the provider protocol, declare an honest capability snapshot, keep the SDK import lazy,
map provider errors to sanitized typed failures, add fake contract tests, and put live calibration
behind an explicit marker and provider-specific opt-in. Never persist SDK response objects or make a
framework default select a remote provider.
