# DarkBooks model adapters

DarkBooks operates on source-grounded scenes and provider-neutral image requests.
The current release supports:

* Text: `TextBackend.complete(system, payload, schema)` using a Chat Completions
  compatible endpoint (`/chat/completions`, `/models`). LM Studio is the tested
  local runtime. JSON Schema or JSON Object can be selected. Long output budgets
  expand on truncation; independent oversized requests are subdivided and cached.
* Images: `image_adapters.image_backend(config, root)` selects a backend implementing
  `validate_workflow()` and `generate(prompt, references, seed, destination)`.
* `comfyui`: the existing API-workflow adapter. The built-in preset is Qwen Image
  2.1 Turbo. Workflow model/encoder/VAE names are configurable. Other architectures
  require an API-format workflow with explicit prompt, seed and output bindings.
* `compatible_images`: `/images/generations`, text-only generation, one image,
  response `data[0].b64_json` or `data[0].url`. No provider-specific SDK, seed control,
  image references, pricing estimates or automatic paid retries are promised.

To add a provider, implement the same image interface and register its key in the
factory. Extend the settings schema and UI provider selector if different inputs
are needed. A model with the same API/workflow can be changed in configuration;
different architectures may need another workflow or adapter.

Projects are immutable after first launch: uploaded source, options, model config,
stage cache and renders live under `data/projects/<id>`. Changing model or prompt
means a new project, so cached results cannot silently mix incompatible settings.
API keys stay in local settings; child workers receive a private environment
snapshot. They are not written into project configs, model caches or exported ZIPs.

GPU ownership: by default, services are external. Optional LM Studio CLI management
loads text before analysis and unloads it before image generation. ComfyUI must
already be running; the app does not install or download model weights. It never
clears another user's Comfy queue or terminates an externally started server.

Experimental face correction remains in the legacy code and is excluded from the
web pipeline. The web version exports original generated scenes in grayscale.
