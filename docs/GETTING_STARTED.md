# Your first illustrated book

[Home](../README.md) · [Русский](GETTING_STARTED.ru.md)

## 1. Open DarkBooks

Download **[DarkBooks-Windows-x64.zip](https://github.com/DarkDan81/DarkBooks/releases/latest/download/DarkBooks-Windows-x64.zip)**
from Releases. Extract the whole folder, then double-click **Start.cmd**. Do not
run it from inside the ZIP. The browser opens automatically; keep the launcher
window open while using the app.

The app includes Python, but not the AI models. Choose one of the following setups.

| Setup | What you need |
|---|---|
| Local | LM Studio for text, ComfyUI for images, downloaded model weights |
| Cloud | A compatible text API and image API, model names and API keys |
| Mixed | Either text or images local, the other connected to a cloud service |

## 2A. Connect local models

### Text: LM Studio

1. Install [LM Studio](https://lmstudio.ai/).
2. Download and load a text model. Our local experiments used
   [Qwen3.5-9B GGUF](https://huggingface.co/lmstudio-community/Qwen3.5-9B-GGUF),
   specifically `Qwen3.5-9B-Q6_K.gguf`. You can try other models that produce
   structured JSON reliably.
3. In LM Studio's **Developer** tab, start the server. Copy its server address.
4. In DarkBooks, open **Connections → Text model**.
5. Enter `http://127.0.0.1:1234/v1` if you use the default local port.
6. Click **Find models**, then choose the loaded model from the Model field.
   A local server usually does not need an API key unless you enabled authentication.

If one GPU is shared between both models, enable **Advanced settings → Load and
unload automatically with LM Studio**. The `lms` command must be available, and
the LM Studio model key must match your downloaded model. DarkBooks then releases
the text model after analysis, before asking ComfyUI to generate images. Leave
this off if you manage the servers yourself.

Official reference: [LM Studio's local server guide](https://lmstudio.ai/docs/developer/core/server).

### Images: ComfyUI

1. Install and start [ComfyUI](https://docs.comfy.org/installation/desktop/windows).
2. Install a ComfyUI version that supports **TextEncodeQwenImage21** for the built-in
   Qwen preset. Custom nodes are not required by this preset.
3. Download the matching files from
   [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1):

| File | Put it in your ComfyUI installation |
|---|---|
| [`qwen_image_2.1_turbo_int8_convrot.safetensors`](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors) | `models/diffusion_models/` |
| [`qwen3vl_8b_w4a8.safetensors`](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/text_encoders/qwen3vl_8b_w4a8.safetensors) | `models/text_encoders/` |
| [`qwen_image_2.1_vae_bf16.safetensors`](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/vae/qwen_image_2.1_vae_bf16.safetensors) | `models/vae/` |

4. Restart ComfyUI after adding the files.
5. In DarkBooks, select **ComfyUI** under Image model. Enter the address shown by
   your ComfyUI installation, for example `http://127.0.0.1:8188`. Desktop and custom
   installations may use a different port—copy yours rather than guessing.
6. Click **Check connection**. Select the checkpoint in Model. In **ComfyUI model
   files**, select the text encoder and VAE listed above. Click **Save connections**.

This preset uses 8 steps and 1024-pixel images. Our local experiments used an RTX
5060 Ti with 16 GB VRAM and 32 GB system RAM. This is a tested setup, not a guarantee
for every model or computer. Weights are large and have their own licenses.

If you see a missing-node message, update ComfyUI or use a workflow compatible with
your installation. Selecting a different model architecture also requires a
matching workflow; changing the checkpoint name alone is not enough.

### Use a different ComfyUI workflow

Before starting an edition, open **Edition options → Custom ComfyUI workflow**.
Export your workflow from ComfyUI in **API format**, select the JSON file and enter
the bindings for your node IDs. For example:

```json
{
  "bindings": {
    "prompt": ["6", "prompt"],
    "seed": ["3", "seed"],
    "references": []
  },
  "output_node": "9"
}
```

The example IDs belong to the built-in preset. Replace them with your workflow's
actual IDs. The output node must return one image. The model files and sampler
settings in a custom workflow come from its JSON; set them in ComfyUI before exporting.

## 2B. Connect cloud APIs

You do not need local model weights or a GPU for this setup.

1. Get the **API base URL**, **model name**, and **API key** from your chosen service.
   A normal chat subscription is not necessarily an API account.
2. In **Text model**, enter the base URL, including `/v1` if your service uses it.
   Enter the exact text model name and key.
3. The text service must support Chat Completions with structured output. The
   default is JSON Schema; if your provider only supports JSON Object, change it
   under **Advanced settings**.
4. Under Image model, select **Compatible image API · cloud**. Enter its base URL,
   image model name and key, then choose an image size supported by that service.
5. Save the connections, then run a **sample chapter** to verify compatibility.

The image adapter expects `POST /images/generations` with `model`, `prompt`, `n`
and `size`, returning `data[0].b64_json` or `data[0].url`. The text adapter uses
`POST /chat/completions`. Services with different APIs need another adapter.
The image connection check saves settings without making a paid generation call.

No external paid provider is bundled or certified. Start small: cloud usage is
billed by your provider and DarkBooks does not estimate the cost yet.

## 3. Make a sample edition

1. Return to **Your library**.
2. Click **Add a book** and choose an `.fb2` file. Or use **Explore a sample** to
   load the original short story *The Lantern Keeper*, included for testing.
3. Enter a short **World & atmosphere** prompt. For example:
   > A weathered coastal town, old lighthouses, quiet mystery, late autumn.
4. Choose an **Illustration style**. The default makes black-and-white ink drawings.
5. Choose the chapters and a target number of illustrations. Two or three is a
   useful starting point. Some chapters may have fewer suitable scenes.
6. For a first-person novel, optionally enter the narrator's name. This connects
   “I” / “Я” to that character.
7. Click **Try one chapter**. It creates a separate sample edition using the first
   selected chapter; your original project stays available for a full run.

The gallery fills as images are completed. Once you like the result, return to
the original project and choose **Illustrate selected chapters**.

## 4. Pause, resume, download

**Pause** finishes and saves the current operation. A model request or image can
take a while; wait for the Paused status. Then choose **Resume edition**.

Closing the browser tab does not stop a worker. If the app or computer crashes,
open DarkBooks again and resume the edition. Completed work is reused. An
interrupted operation may need to run again; this is not sampler-step resume.

After completion, choose **Download your book**. The full original text is retained,
including chapters you did not select. The exported illustrations are grayscale.
**Save illustrations** downloads the original generated images separately.

Settings for a started edition are fixed to keep its cached results consistent.
To try a different prompt or model, add the book again as a new edition.

## Common questions

**The app cannot reach a model.** Start the model server, check its address and
port, and use Find models / Check connection. `localhost` means this computer.

**ComfyUI runs out of memory.** Unload your text model first, enable LM Studio
automatic loading, or move one model to another machine/cloud service. DarkBooks
does not download or optimize model weights for you.

**I only have EPUB or PDF.** This release reads and exports FB2. Convert your book
to FB2 with a tool you already use before importing it.

**My characters look different between pictures.** Character consistency is a
known limitation. The web app uses original generated scenes; it does not replace faces.

**Where are my files?** In the `data` folder beside the portable app. Source/pip
installs may use `~/DarkBooks`; `DARKBOOKS_DATA` overrides the location. Keys are
plain-text local settings. Do not share the populated data folder.

**Can I switch languages?** Use EN / РУ in the sidebar. Your book, prompts and
chapter selection do not change when the interface language changes.

**Something else went wrong?** Expand Activity log, then
[open an issue](https://github.com/DarkDan81/DarkBooks/issues/new/choose) with the
app version, model names, and a sanitized error. Do not post your keys or book.
