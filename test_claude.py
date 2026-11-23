"""CLI diagnostic tool to verify Anthropic Claude API connectivity for BEN."""

import sys
import argparse
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Add project root to sys.path
PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

from ben.config import settings
from ben.core.client import AnthropicClientManager

console = Console()


def main():
    parser = argparse.ArgumentParser(description="Test BEN's connection to the Anthropic Claude API.")
    parser.add_argument(
        "--prompt",
        type=str,
        default="Introduce yourself briefly as BEN, powered by Anthropic Claude.",
        help="Custom test prompt to send to Claude."
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override Claude model identifier (e.g., claude-3-5-sonnet-20241022, claude-3-7-sonnet-20250219)."
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List official supported Claude models."
    )
    args = parser.parse_args()

    console.print(Panel.fit(
        "[bold cyan]BEN - Anthropic Claude API Authentication & Connectivity Diagnostic[/bold cyan]\n"
        "[dim]Testing Anthropic Claude API integration, model configuration, and environment setup[/dim]",
        border_style="cyan"
    ))

    # Safe Key & Environment Diagnostics (Never prints key or any substring)
    diag = settings.get_api_key_diagnostics()

    table = Table(title="Authentication & Environment Diagnostics", border_style="blue")
    table.add_column("Property", style="bold")
    table.add_column("Diagnostic Value", style="cyan")

    table.add_row(".env File Present", "[green]Yes[/green]" if diag["env_file_exists"] else "[yellow]No (.env not found)[/yellow]")
    table.add_row("Key Configured", "[green]Yes[/green]" if diag["key_exists"] else "[red]No[/red]")
    table.add_row(
        "Key Character Count",
        f"[green]{diag['char_count']} characters[/green]" if diag["key_exists"] else "[red]0 characters[/red]"
    )
    table.add_row(
        "Key Format Check",
        "[yellow]Placeholder Detected[/yellow]" if diag["is_placeholder"] else ("[green]Valid (Non-placeholder)[/green]" if diag["key_exists"] else "[red]Missing[/red]")
    )
    table.add_row("Key Source", diag["source"])
    table.add_row("Base URL", diag.get("base_url", "default"))
    table.add_row("Configured Model", args.model or settings.ben_model)
    table.add_row("Backend Environment", settings.ben_env)
    table.add_row("Host / Port", f"{settings.ben_host}:{settings.ben_port}")
    console.print(table)
    console.print()

    # Client manager check
    client_mgr = AnthropicClientManager(model=args.model)

    if not client_mgr.is_configured:
        console.print(Panel(
            "[bold yellow]Anthropic API Key Missing or Placeholder Detected![/bold yellow]\n\n"
            "Action required:\n"
            "1. Open [cyan].env[/cyan] in your editor:\n"
            "   [bold]notepad .env[/bold]\n"
            "2. Set [cyan]ANTHROPIC_API_KEY[/cyan] to your valid Anthropic API key.\n"
            "3. Optionally set [cyan]ANTHROPIC_BASE_URL[/cyan] if using a proxy.\n"
            "4. Re-run this diagnostic:\n"
            "   [bold].venv\\Scripts\\python test_claude.py[/bold]",
            title="Setup Action Required",
            border_style="yellow"
        ))
        return 1

    # Live Connection Test
    console.print(f"[bold]Connecting to Claude using model: [cyan]{client_mgr.default_model}[/cyan]...[/bold]")
    result = client_mgr.test_connection(prompt=args.prompt)

    if result.success:
        safe_response = (result.response or "").encode("cp1252", "replace").decode("cp1252")
        console.print(Panel(
            f"[bold green]CONNECTION SUCCESSFUL![/bold green]\n\n"
            f"[bold]Model:[/bold] {result.model}\n"
            f"[bold]Latency:[/bold] {result.latency_ms} ms\n"
            f"[bold]Key Length Verified:[/bold] {result.char_count} characters\n\n"
            f"[bold cyan]BEN Response:[/bold cyan]\n"
            f"{safe_response}",
            title="API Diagnostic Result",
            border_style="green"
        ))

        if args.list_models:
            console.print("\n[bold]Supported Claude models:[/bold]")
            for m in client_mgr.list_models():
                console.print(f"  • {m}")

        return 0
    else:
        console.print(Panel(
            f"[bold red]CONNECTION FAILED[/bold red]\n\n"
            f"[bold]Model:[/bold] {result.model}\n"
            f"[bold]Latency:[/bold] {result.latency_ms} ms\n"
            f"[bold]Key Character Count:[/bold] {result.char_count}\n\n"
            f"[bold red]Error Details:[/bold red]\n"
            f"{result.error}\n\n"
            f"[dim]Please verify your ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, and network connectivity.[/dim]",
            title="Diagnostic Error",
            border_style="red"
        ))
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
