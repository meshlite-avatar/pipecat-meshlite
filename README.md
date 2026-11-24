# pipecat-meshlite

Give your [Pipecat](https://github.com/pipecat-ai/pipecat) bot a lip-synced
[MeshLite](https://meshlite.io) face. The face is drawn in the visitor's browser by the
MeshLite web SDK, from the audio track your page already receives from the bot. Your bot
keeps its own speech-to-text, LLM, voice and transport: no video server, no GPU, no
avatar participant, and no MeshLite voice minutes.

```
your Pipecat bot --audio track + RTVI events--> visitor page --> MeshLite face (lips in the browser)
                 <--"meshlite-session" request--   (pipecat-client-js + MeshLite web SDK)
```

This package holds the server side:

- **`MeshLiteAvatarProcessor`**: a pass-through pipeline processor. It answers the page's
  `meshlite-session` RTVI request with a MeshLite session created with your secret key,
  and tells the page when the bot's language changes, so the lip model follows. It never
  changes, delays or copies the bot's audio.
- **`MeshLiteClient`**: creates MeshLite client sessions (`audio: "track"`) for any web
  route, if you prefer to give the page its session over HTTP.

Maintained by Toda Labs, the company that makes MeshLite.

Tested with Pipecat v1.16.0, `@pipecat-ai/client-js` 1.16.0 and the MeshLite web SDK 1.11.60.

## Installation

```bash
uv add pipecat-meshlite
# or: pip install pipecat-meshlite
```

## Prerequisites

In the [MeshLite Console](https://console.meshlite.io) (Projects, then **For developers**):

1. In **Allowed sites**, add the address of your page (for a local test,
   `http://localhost:7860`).
2. In **Keys**, create a publishable key and a secret key of the same environment (test
   or live).
3. In **Code**, copy your avatar id.

| Variable | Required | Meaning |
|---|---|---|
| `MESHLITE_PUBLISHABLE_KEY` | yes | `ml_pk_test_...` or `ml_pk_live_...` (also sent to the page) |
| `MESHLITE_SECRET_KEY` | yes | `ml_sk_...` of the same environment; stays on your server |
| `MESHLITE_ORIGIN` | yes | address of your page, for example `https://www.example.com` |
| `MESHLITE_AVATAR` | recommended | avatar id; the page must mount the same one (the example requires it). Without it, sessions get the plan's default avatar |
| `MESHLITE_API` | no | another MeshLite environment (default `https://console.meshlite.io`) |

## Usage

### 1. The bot: add the processor

```python
from pipecat.pipeline.pipeline import Pipeline
from pipecat.transcriptions.language import Language
from pipecat_meshlite import MeshLiteAvatarProcessor

avatar = MeshLiteAvatarProcessor(language=Language.EN)  # reads MESHLITE_* from the environment

pipeline = Pipeline([
    transport.input(),
    stt,
    user_aggregator,
    llm,
    tts,
    avatar,              # after the TTS: it reads what the bot says
    transport.output(),
    assistant_aggregator,
])
```

RTVI must be on (the default of `PipelineWorker`). Tested with the SmallWebRTC transport;
the processor only uses RTVI messages, so other browser transports should work too.

**Lip language.** The processor reads the words the bot speaks (`TTSTextFrame`). As soon
as a reply is clearly in English, French or Mandarin and differs from the current lip
model, it sends `meshlite-language` to the page. A bot that switches language on purpose
can call `await avatar.set_language("fr")`. The lips follow the bot's voice, not the
visitor's speech-to-text.

| Parameter | Default | Meaning |
|---|---|---|
| `client` | from the environment | a `MeshLiteClient` (keys, origin, avatar, API) |
| `avatar` | client's avatar | avatar id for this bot |
| `language` | `None` | language the bot speaks; picks the lip model (`en`, `fr`, `zh-CN`, else `auto`) |
| `max_sessions` | `3` | most session requests to MeshLite per connection, refusals included (each session counts as a conversation) |
| `follow_language` | `True` | send `meshlite-language` when the language the bot speaks changes |

Event handlers: `on_session_created(processor, session)` and
`on_session_error(processor, error)`. If MeshLite refuses a session, the page gets an
error response (`meshlite_session_refused:<code>`, final, or
`meshlite_session_unavailable:<code>`, worth another try), the processor pushes a
non-fatal `ErrorFrame`, and the visitor keeps hearing the bot without the face.

### 2. The page: mount the face and attach the Pipecat client

```html
<div id="avatar" style="width:340px;height:510px"></div>
<script src="https://console.meshlite.io/sdk/1.11.60/meshlite.js" crossorigin="anonymous"></script>
<script type="module">
  import { PipecatClient } from "@pipecat-ai/client-js";
  import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport";

  let botReady;
  const ready = new Promise((resolve) => { botReady = resolve; });
  const client = new PipecatClient({
    transport: new SmallWebRTCTransport(), enableMic: true,
    callbacks: {
      onBotReady: () => botReady(),
      onServerMessage: (m) => { if (m?.type === "meshlite-language") avatar.setLanguage(m.language); },
    },
  });
  const avatar = MeshLite.mount({
    container: "#avatar", avatar: "your-avatar-id", publishableKey: "ml_pk_live_...", audio: "track",
    // Stays under the SDK's 15 s wait: at most 4 s for the bot, then 10 s for the session.
    sessionToken: async () => {
      await Promise.race([ready, new Promise((_, fail) => setTimeout(() => fail(new Error("bot not ready")), 4000))]);
      try {
        const reply = await client.sendClientRequest("meshlite-session", {}, 10000);
        return (reply?.d ?? reply).sessionToken;
      } catch (e) {
        const error = e?.data?.error ?? e?.message ?? String(e);
        // A refusal or the session limit will not change: the SDK must not ask again.
        throw Object.assign(new Error(error), { retryable: !/^meshlite_session_(refused|limit)/.test(error) });
      }
    },
  });
  avatar.attachPipecat(client);              // before connect, so no speech is missed
  await client.connect({ webrtcUrl: "/api/offer" });
</script>
```

`attachPipecat` takes the bot's audio track and follows Pipecat's RTVI events
(`botStartedSpeaking`, `botStoppedSpeaking`, `botInterrupted`, ...). On an interruption
the face stops at once and does not replay the cut sentence. Keep your usual bot audio
element: the SDK mutes it while the face carries the voice and unmutes it whenever the
face cannot. Full page guide: [meshlite.io/docs#pipecat](https://meshlite.io/docs#pipecat).

### Session over HTTP instead

```python
from pipecat_meshlite import MeshLiteClient

meshlite = MeshLiteClient()

@app.post("/api/meshlite-session")
async def meshlite_session():
    session = await meshlite.create_session(language="en")
    return session.to_client()   # {sessionToken, publishableKey, avatar, language}
```

Authenticate your visitors and rate-limit this route: each call creates a conversation.

## Run the example

[`examples/bot.py`](examples/bot.py) is a complete bot in one file: Azure speech-to-text,
Azure OpenAI and Azure text-to-speech over SmallWebRTC, with the MeshLite face page served
at `/`.

```bash
git clone https://github.com/meshlite-avatar/pipecat-meshlite && cd pipecat-meshlite
uv sync --extra example
cp examples/env.example examples/.env     # fill in the Azure and MeshLite values
uv run examples/bot.py
```

Open http://localhost:7860, press **Start** and allow the microphone. The face greets you
and answers with matching lips. Interrupt it by speaking: it stops at once. The face
starts once it has received a little of the voice (a live track has no "segment ready"
signal); until the face is ready, the page plays the voice itself, so the conversation
starts without waiting for the face.

Any other Pipecat services work the same way: the processor does not depend on them.

## Evals

[`evals/meshlite_bot.yaml`](evals/meshlite_bot.yaml) runs the example under
`pipecat eval` (greeting, an answer, and an interruption), judged by Azure OpenAI:

```bash
uv sync --extra example --extra eval
uv run examples/bot.py                    # in one terminal
cd evals && set -a && . ../examples/.env && set +a
PYTHONPATH=. uv run pipecat eval run meshlite_bot.yaml -v
```

## Tests

```bash
uv sync --extra test
uv run pytest
```

## Limits

- One lip model per language: English, French, Mandarin Chinese. Other languages use the
  avatar's own language model.
- A MeshLite session lasts at most 2 hours and counts as one conversation of your plan.

## License

Apache-2.0. See [LICENSE](LICENSE).
