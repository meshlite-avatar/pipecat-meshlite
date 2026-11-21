"""Judge for `pipecat eval`: Azure OpenAI instead of the default local Ollama model.

Reads the same `.env` as the example (`pipecat eval` loads the nearest one).
"""

import os

from pipecat.services.azure.llm import AzureLLMService


def judge(config: dict) -> AzureLLMService:
    """Return the LLM that judges `eval:` criteria and explains failures."""
    return AzureLLMService(
        api_key=os.environ["AZURE_API_KEY"],
        endpoint=f"{os.environ['AZURE_OPENAI_ENDPOINT'].rstrip('/')}/openai/v1",
        settings=AzureLLMService.Settings(model=config.get("model") or "gpt-5.4-mini"),
    )
