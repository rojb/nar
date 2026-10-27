"""Send one prompt to an LLM provider and print the internal response.

Developer tool to check the adapters before the agent loop exists:

    python scripts/run_provider.py --provider ollama --model qwen3.5:4b "Say hello"
    python scripts/run_provider.py --with-tool --no-stream "What is the weather in La Paz?"

Provider and model come from the nar configuration; the flags override it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from nar.config import ProviderName, load_config
from nar.errors import NarError
from nar.providers import LLMResponse, Message, ResponseDone, TextDelta, create_provider

WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Get the current weather for a city.",
    "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name, e.g. La Paz"}},
        "required": ["city"],
    },
}


@click.command()
@click.option("--provider", type=click.Choice([name.value for name in ProviderName]))
@click.option("--model")
@click.option("--no-stream", is_flag=True, help="Use complete() instead of stream().")
@click.option("--with-tool", is_flag=True, help="Offer the sample get_weather tool.")
@click.option("--system", help="Optional system prompt.")
@click.argument("prompt")
def run(
    provider: str | None,
    model: str | None,
    no_stream: bool,
    with_tool: bool,
    system: str | None,
    prompt: str,
) -> None:
    config = load_config(Path.cwd(), {"provider": provider, "model": model}).config
    llm = create_provider(config, on_retry=lambda n: click.secho(str(n), fg="yellow", err=True))
    click.secho(f"{llm.name} · {llm.model} · {'complete' if no_stream else 'stream'}", dim=True)
    messages = [Message.user(prompt)]
    tools = [WEATHER_TOOL] if with_tool else []
    if no_stream:
        response = llm.complete(messages, system=system, tools=tools)
        click.echo(response.content)
    else:
        response = LLMResponse("")
        for event in llm.stream(messages, system=system, tools=tools):
            if isinstance(event, TextDelta):
                click.echo(event.text, nl=False)
            elif isinstance(event, ResponseDone):
                response = event.response
        click.echo()
    _summary(response)


def _summary(response: LLMResponse) -> None:
    click.secho("---", dim=True)
    for call in response.tool_calls:
        click.echo(f"tool call: {call.name} {json.dumps(call.arguments)}  (id {call.id})")
    click.echo(f"stop reason: {response.stop_reason}")
    if response.usage is not None:
        usage = response.usage
        click.echo(f"usage: {usage.input_tokens} input, {usage.output_tokens} output tokens")


if __name__ == "__main__":
    try:
        run(standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
        sys.exit(exc.exit_code)
    except NarError as exc:
        click.secho(f"error: {exc.message}", fg="red", err=True)
        sys.exit(exc.exit_code)
