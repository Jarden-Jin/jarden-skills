---
name: jarden-image
description: Generate images through Jarden's authenticated remote MOSS or H3 Image API when the user asks for a Jarden image, image estimate, or image-job recovery. Do not use for local model deployment, video generation, or unrelated image editing.
---

# Jarden Image

Use the bundled, standard-library Python client at `scripts/client.py`. This entire skill directory can be copied to another computer. It requires Python 3.12+ and a personal service Key on first use; it has no weights or server.

## First-run credential setup

With `--token-file`, the client uses that existing device Key directly.
Otherwise it reads a personal Key from `--personal-key-file`, `HOME_INFERENCE_PERSONAL_KEY_FILE`, or the shared local cache.
If absent, it prompts once without echoing the value.
It verifies the personal Key using `/v1/auth` before caching it; invalid values are not remembered.
An explicitly supplied key file is copied into the cache only after validation.

Both Skills share `%APPDATA%/JardenAI/hosts/<service-namespace>/` on Windows or `~/.config/jarden-ai/hosts/<service-namespace>/` elsewhere (`XDG_CONFIG_HOME` is honored). The namespace isolates the full service URL, including port and path. This directory holds `personal.key`, `device.key`, and non-secret account/device metadata. Keys are written atomically with private file permissions, outside this Skill.

The client validates the cached device Key.
If it is missing, revoked, or belongs to another account, the verified personal Key creates a new device Key through `/v1/keys`.
A device from another personal account is not reused.
Both Skills reuse the same valid device Key on this computer.

`--device-key-file` and `HOME_INFERENCE_DEVICE_KEY_FILE` override its path.
`--device-label` and `HOME_INFERENCE_DEVICE_LABEL` set its registration label.
Personal and device Keys are never printed.
On older servers without `/v1/auth`, access is checked with `/v1/models` and local account metadata.

Default service: `https://ai.comark.work`. To use another installation, supply `--base-url` or set `HOME_INFERENCE_BASE_URL`. After the first personal-Key setup, normal commands need no credential options. Existing device-Key files remain supported through `--token-file` / `HOME_INFERENCE_TOKEN_FILE`. Do not put a Key itself in a command, prompt, generated artifact, or this skill.

## Generate

Resolve `scripts/client.py` relative to this skill. Start with `models` to check whether the selected model and parameters are available. `image --model moss` selects `image-moss-sft`; `image --model h3` selects the dedicated `image-h3-t2i` endpoint. H3 Image availability is determined by the catalog; do not substitute H3 video weights or promise readiness when the endpoint is absent or not ready.

```text
python scripts/client.py models
python scripts/client.py estimate --kind image --model moss --width 1024 --height 1024 --steps 25
python scripts/client.py image --model moss --prompt "Blue ceramic cup on a white background, soft natural light" --seed 12345 --width 1024 --height 1024 --steps 25 --key image-cup-001 --wait --out /absolute/outputs
```

On Windows use the available interpreter, such as `py -3.13`, and actual Windows paths.
Read model limits and defaults from `models` before overriding settings.
MOSS defaults to 1024², 25 steps, and CFG 2.5.
The dedicated H3 Image workflow defaults to 2048², 6 steps, CFG 1, and at least 4 megapixels.
Use the current catalog and job results to determine availability.

Images use the `standard` sampling profile. `--sampling-profile standard` may be supplied explicitly when supported by the catalog; `turbo-4step` is video-only and must not replace the MOSS 25-step or H3 Image 6-step defaults. Estimate responses report the sample count and observed timing range; one sample or extrapolation carries low confidence.

`--attention-backend` selects a video backend. Image requests keep the default PyTorch behavior; do not use `comfy-kitchen` for an image. The client rejects image requests with that backend and omits a redundant `pytorch` field from image payloads.

```text
python scripts/client.py image --model h3 --prompt "A blue ceramic cup, soft studio light" --width 2048 --height 2048 --steps 6 --cfg 1 --key h3-cup-001 --wait --out /absolute/outputs
```

## Recover and download

```text
python scripts/client.py status job_ACTUAL_ID
python scripts/client.py download job_ACTUAL_ID --wait --out /absolute/outputs
python scripts/client.py resume-request --file /absolute/outputs/request-ACTUAL.json --wait --out /absolute/outputs
```

Use `resume-request` only when a submission reply was lost, with the same account and original saved request. It reuses the idempotency key. Do not change the payload or key while checking an uncertain submission. If the job is `submission_unknown`, preserve its ID and ask the operator to reconcile it; do not resubmit under a new key. A deliberately different prompt or seed needs a new key.

401 means missing/invalid/revoked Key; 404 means absent or another user's resource; 429 requires waiting; 503 means backend/model/workflow not ready. A ready health or catalog response does not prove successful inference. Report actual job status and downloaded artifacts; distinguish mocked checks from real images.
