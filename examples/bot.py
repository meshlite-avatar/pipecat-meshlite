#
# Copyright 2026 Toda Labs (MeshLite)
#
# SPDX-License-Identifier: Apache-2.0
#

"""A Pipecat voice bot with a MeshLite face, in one file.

The bot is a standard Pipecat pipeline: Azure speech-to-text, an Azure OpenAI LLM and
Azure text-to-speech, over SmallWebRTC. `MeshLiteAvatarProcessor` gives the page its
MeshLite session over RTVI; the page hands the bot's audio track to the MeshLite web SDK
(`avatar.attachPipecat(client)`), which draws the lip-synced face in the browser. The
bot's audio is not changed: no video server, no GPU.

Run:

    uv run bot.py            # then open http://localhost:7860

The same file runs under `pipecat eval` (the "eval" transport below).
"""

import json
import os

from dotenv import load_dotenv
from fastapi.responses import HTMLResponse
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.evals.transport import EvalTransportParams
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.run import app
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.azure.llm import AzureLLMService
from pipecat.services.azure.stt import AzureSTTService
from pipecat.services.azure.tts import AzureTTSService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.runner import WorkerRunner

from pipecat_meshlite import MeshLiteAvatarProcessor

load_dotenv(override=True)

MESHLITE_API = os.getenv("MESHLITE_API", "https://console.meshlite.io").rstrip("/")
MESHLITE_SDK_VERSION = os.getenv("MESHLITE_SDK_VERSION", "1.11.60")

transport_params = {
    "eval": lambda: EvalTransportParams(audio_in_enabled=True, audio_out_enabled=True),
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}

SYSTEM_INSTRUCTION = (
    "You are a friendly assistant with a face, talking with a visitor by voice. Your "
    "replies are spoken aloud: no emojis, lists or formatting. Answer in one to three "
    "short sentences unless the visitor asks for more. If the visitor interrupts you, "
    "stop and answer their new question."
)


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    """Build and run the pipeline for one visitor."""
    stt = AzureSTTService(
        api_key=os.environ["AZURE_API_KEY"],
        region=os.environ["AZURE_REGION"],
        settings=AzureSTTService.Settings(language=Language.EN_US),
    )
    llm = AzureLLMService(
        api_key=os.environ["AZURE_API_KEY"],
        endpoint=f"{os.environ['AZURE_OPENAI_ENDPOINT'].rstrip('/')}/openai/v1",
        settings=AzureLLMService.Settings(
            model=os.getenv("AZURE_OPENAI_MODEL", "gpt-5.4-mini"),
            system_instruction=SYSTEM_INSTRUCTION,
        ),
    )
    tts = AzureTTSService(
        api_key=os.environ["AZURE_API_KEY"],
        region=os.environ["AZURE_REGION"],
        settings=AzureTTSService.Settings(voice="en-US-AvaMultilingualNeural"),
    )

    # The MeshLite face: answers the page's "meshlite-session" request (MESHLITE_* keys
    # stay on this server) and follows the bot's language. The audio passes untouched.
    avatar = MeshLiteAvatarProcessor(language=Language.EN)

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            tts,
            avatar,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
        processor_unusable_policy=ProcessorUnusablePolicy.END,
    )
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    @avatar.event_handler("on_session_created")
    async def on_session_created(_avatar, session):
        logger.info(f"MeshLite session created (lip model: {session.language or 'auto'})")

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client):
        logger.info("Visitor connected")
        context.add_message(
            {"role": "developer", "content": "Greet the visitor in one short sentence."}
        )
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        logger.info("Visitor left")
        await runner.cancel()

    await runner.run()


async def bot(runner_args: RunnerArguments):
    """Bot entry point for the Pipecat runner and Pipecat Cloud."""
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


# The visitor page. Registered before the runner starts, so it takes "/" (the runner's
# own voice-only page stays at /client).
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pipecat bot with a MeshLite face</title>
<style>
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #0f1115;
         color: #e8e8e8; font: 16px/1.4 system-ui, sans-serif; }
  main { width: min(360px, 100% - 32px); text-align: center; }
  #avatar { width: 100%; aspect-ratio: 2 / 3; border-radius: 16px; overflow: hidden; background: #1a1d24; }
  #status { min-height: 2.8em; margin: 12px 0; color: #b8bcc6; }
  button { font: inherit; padding: 10px 18px; border: 0; border-radius: 999px; cursor: pointer;
           background: #4f7cff; color: #fff; }
  button:disabled { opacity: .45; cursor: default; }
</style></head>
<body><main>
  <div id="avatar"></div>
  <p id="status">Press Start and allow the microphone.</p>
  <button id="start">Start</button> <button id="end" disabled>End</button>
  <button id="sound" hidden>Enable sound</button>
  <audio id="bot-audio" autoplay></audio>
</main>
<script src="__SDK__" crossorigin="anonymous"></script>
<script type="module">
  import { PipecatClient } from "https://cdn.jsdelivr.net/npm/@pipecat-ai/client-js@1.16.0/+esm";
  import { SmallWebRTCTransport } from "https://cdn.jsdelivr.net/npm/@pipecat-ai/small-webrtc-transport@1.11.2/+esm";
  const CONFIG = __CONFIG__;
  const $ = (id) => document.getElementById(id);
  const say = (text) => { $("status").textContent = text; };
  let avatar, client, link;

  async function start() {
    $("start").disabled = true;
    say("Connecting...");
    let botReady;
    const ready = new Promise((resolve) => { botReady = resolve; });
    client = new PipecatClient({
      transport: new SmallWebRTCTransport(), enableMic: true, enableCam: false,
      callbacks: {
        onBotReady: () => botReady(),
        onTrackStarted: (track, participant) => {
          if (track.kind === "audio" && !participant?.local) $("bot-audio").srcObject = new MediaStream([track]);
        },
        // The bot changed language: the lips follow (pipecat-meshlite, "meshlite-language").
        onServerMessage: (m) => { if (m?.type === "meshlite-language") avatar?.setLanguage(m.language); },
        onBotDisconnected: () => end("The bot left."),
      },
    });
    avatar = MeshLite.mount({
      container: "#avatar", avatar: CONFIG.avatar, publishableKey: CONFIG.publishableKey, audio: "track",
      // The bot creates the MeshLite session (pipecat-meshlite, "meshlite-session").
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
    link = avatar.attachPipecat(client); // before connect, so no speech is missed
    link.on("ready", ({ output }) => say(`Avatar ready (voice played by the ${output}). Say hello!`));
    link.on("fallback", ({ code }) => say(`Avatar unavailable (${code}): the voice continues.`));
    link.on("audio-blocked", () => { $("sound").hidden = false; });
    try {
      await client.connect({ webrtcUrl: "/api/offer" });
      $("end").disabled = false;
    } catch (error) { end(`Could not start: ${error.message || error}`); }
  }

  async function end(message) {
    try { await client?.disconnect(); } catch {}
    try { avatar?.unmount(); } catch {}
    avatar = client = link = null;
    $("bot-audio").srcObject = null;
    $("end").disabled = true; $("start").disabled = false; $("sound").hidden = true;
    if (message) say(message);
  }

  $("start").addEventListener("click", start);
  $("end").addEventListener("click", () => end("Conversation ended."));
  $("sound").addEventListener("click", async () => { await link?.resumeAudio(); $("sound").hidden = true; });
</script></body></html>"""


@app.get("/", include_in_schema=False)
async def page():
    """Serve the visitor page with the MeshLite SDK and the public MeshLite settings."""
    config = {
        "publishableKey": os.getenv("MESHLITE_PUBLISHABLE_KEY", ""),
        # Required: the page and the session must use the same avatar.
        "avatar": os.environ["MESHLITE_AVATAR"],
    }
    html = PAGE.replace("__SDK__", f"{MESHLITE_API}/sdk/{MESHLITE_SDK_VERSION}/meshlite.js")
    html = html.replace("__CONFIG__", json.dumps(config))
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
