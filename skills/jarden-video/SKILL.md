---
name: jarden-video
description: Generate H3 videos from text or a first frame through Jarden's authenticated remote API when the user asks for a Jarden video, estimate, or video-job recovery. Do not use for local model deployment, image-only generation, or unrelated video editing.
---

# Jarden Video

For first-frame video, use an image with the same aspect ratio as the requested output. The H3 conditioning node stretches the first frame to the generation canvas; a square input used for landscape output will look wider. The service does not automatically crop or letterbox it.

Use `scripts/client.py`, bundled here and requiring only Python 3.12+ standard library. Copy this whole skill directory to use it elsewhere. The server, GPU, and weights are not part of this skill.

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

Default service: `https://ai.comark.work`. Set `--base-url` or `HOME_INFERENCE_BASE_URL` for another installation. Never paste a Key into commands, prompts, reports, or this directory.

## Parameters and estimates

Run `models` to read current availability and limits. `video` selects `video-h3-t2v`; adding `--first-frame <asset_id>` selects `video-h3-i2v`. Video does not support CFG.

```text
python scripts/client.py models
python scripts/client.py estimate --kind video --width 1920 --height 1080 --duration-seconds 10 --steps 20 --render-mode upscale
python scripts/client.py video --prompt "A blue cup on a table. The camera slowly moves right. Soft morning light and quiet room ambience. No dialogue." --seed 12345 --width 864 --height 480 --duration-seconds 5 --steps 20 --key video-cup-001 --wait --out /absolute/outputs
```

On Windows replace `python` with the installed interpreter, such as `py -3.13`, and use actual Windows paths. `native` renders at the requested resolution; `upscale` generates near 480p and enlarges the output, so identify it as upscaled. Estimates may be null until real successful tasks exist. Never promise 1080p/10s in five minutes without a measured result on the host.

The default sampling profile is `standard`, normally 20 steps.
If `models` lists `turbo-4step`, select it with `--sampling-profile turbo-4step`.
The client fixes Turbo to 4 steps and rejects a conflicting `--steps`.
The profile works with text video and first-frame video.
Published timing samples are not completion guarantees.
Native output and other backends need separate evidence.

Video attention defaults to `pytorch`.
When declared by `parameters.attention_backend.options`, select `pytorch` or `comfy-kitchen`.
The measured fast combination is Turbo 4 steps, upscale, and Comfy Kitchen.
It first generates near 480p and then enlarges the result.
Estimates keep each backend separate.
Do not promise native 1080p timing without a matching successful sample.

```text
python scripts/client.py estimate --kind video --sampling-profile turbo-4step --width 1920 --height 1080 --duration-seconds 10 --render-mode upscale
python scripts/client.py video --sampling-profile turbo-4step --prompt "Slow camera motion around a blue ceramic cup, soft daylight, quiet ambience" --key turbo-cup-001 --wait --out /absolute/outputs
```

## First frame

```text
python scripts/client.py upload --file /absolute/reference.png
python scripts/client.py video --first-frame asset_ACTUAL_ID --prompt "Slow camera motion, consistent subject, quiet natural ambience" --key first-frame-001 --wait --out /absolute/outputs
```

Use the actual returned asset ID and the same user's Key. Supported inputs: PNG/JPEG/WebP, at most 20MB and 25MP.

## Recover and download

```text
python scripts/client.py status job_ACTUAL_ID
python scripts/client.py download job_ACTUAL_ID --wait --out /absolute/outputs
python scripts/client.py resume-request --file /absolute/outputs/request-ACTUAL.json --wait --out /absolute/outputs
```

The client saves the exact request before sending and saves returned job records. A closed client does not cancel the server job. Downloaded MP4 files are checked against the server's SHA256; existing matching files are reused.

After a lost submission reply, recover using the same account and saved request/idempotency key. Do not create a new key to solve uncertainty. `submission_unknown` requires the operator to reconcile the backend, not automatic resubmission. Change the key only for a deliberately new generation. 401 is invalid credentials, 404 absent/foreign resources, 429 wait, and 503 not ready.

Catalog and health readiness do not prove video inference, audio, quality, or real external-network access. Report actual completed jobs, downloaded results, and whether any test used a mock backend.
