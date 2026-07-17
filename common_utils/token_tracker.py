import threading
import json
from pathlib import Path
from rich.table import Table
from rich.console import Console
from rich.panel import Panel

class TokenTracker:
    def __init__(self):
        self.lock = threading.Lock()
        self.current_node = None
        # Maps node_name -> {"input_tokens": int, "output_tokens": int, "calls": int, "models": set}
        self.usage = {}

    def set_current_node(self, node_name):
        with self.lock:
            self.current_node = node_name
            if node_name and node_name not in self.usage:
                self.usage[node_name] = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "calls": 0,
                    "models": set()
                }

    def record_usage(self, model_name: str, input_tokens: int, output_tokens: int):
        with self.lock:
            node = self.current_node or "unknown"
            if node not in self.usage:
                self.usage[node] = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "calls": 0,
                    "models": set()
                }
            self.usage[node]["input_tokens"] += input_tokens
            self.usage[node]["output_tokens"] += output_tokens
            self.usage[node]["calls"] += 1
            self.usage[node]["models"].add(model_name)

    def get_summary_dict(self):
        with self.lock:
            summary = {}
            total_input = 0
            total_output = 0
            total_calls = 0
            
            for node_name, data in self.usage.items():
                input_t = data["input_tokens"]
                output_t = data["output_tokens"]
                total_t = input_t + output_t
                calls = data["calls"]
                
                total_input += input_t
                total_output += output_t
                total_calls += calls
                
                summary[node_name] = {
                    "input_tokens": input_t,
                    "output_tokens": output_t,
                    "total_tokens": total_t,
                    "calls": calls,
                    "models": list(data["models"])
                }
            
            summary["total"] = {
                "input_tokens": total_input,
                "output_tokens": total_output,
                "total_tokens": total_input + total_output,
                "calls": total_calls
            }
            return summary

    def print_summary(self):
        console = Console()
        with self.lock:
            if not self.usage:
                console.print("\n[yellow]No token usage recorded during this run.[/yellow]")
                return
            
            table = Table(title="Token Usage Summary per Node", show_footer=True)
            table.add_column("Node Name", footer="Total")
            table.add_column("LLM Model(s) Used")
            table.add_column("API Calls", justify="right")
            table.add_column("Input Tokens", justify="right")
            table.add_column("Output Tokens", justify="right")
            table.add_column("Total Tokens", justify="right")
            
            total_calls = 0
            total_input = 0
            total_output = 0
            
            for node_name, data in self.usage.items():
                models = ", ".join(data["models"]) or "None"
                calls = data["calls"]
                input_t = data["input_tokens"]
                output_t = data["output_tokens"]
                total_t = input_t + output_t
                
                total_calls += calls
                total_input += input_t
                total_output += output_t
                
                table.add_row(
                    node_name,
                    models,
                    str(calls),
                    f"{input_t:,}",
                    f"{output_t:,}",
                    f"{total_t:,}"
                )
                
            total_all = total_input + total_output
            table.columns[2].footer = str(total_calls)
            table.columns[3].footer = f"{total_input:,}"
            table.columns[4].footer = f"{total_output:,}"
            table.columns[5].footer = f"{total_all:,}"
            
            console.print("\n")
            console.print(Panel(
                table,
                title="[bold green]Token Usage Report[/bold green]",
                border_style="green",
                expand=False
            ))

    def save_to_json(self, filepath="reports/token_usage.json"):
        try:
            Path(filepath).parent.mkdir(exist_ok=True, parents=True)
            summary_dict = self.get_summary_dict()
            Path(filepath).write_text(
                json.dumps(summary_dict, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

token_tracker = TokenTracker()

def extract_tokens(response, messages) -> tuple[int, int]:
    """Extract input and output tokens from a LangChain response, falling back to heuristic if missing."""
    input_tokens = 0
    output_tokens = 0
    
    # Try usage_metadata first
    if hasattr(response, 'usage_metadata') and response.usage_metadata:
        input_tokens = response.usage_metadata.get('input_tokens', 0)
        output_tokens = response.usage_metadata.get('output_tokens', 0)
        
    # Try response_metadata
    if (input_tokens == 0 or output_tokens == 0) and hasattr(response, 'response_metadata') and response.response_metadata:
        meta = response.response_metadata
        # Check standard token_usage/usage structures
        if 'token_usage' in meta and isinstance(meta['token_usage'], dict):
            input_tokens = meta['token_usage'].get('prompt_tokens', 0)
            output_tokens = meta['token_usage'].get('completion_tokens', 0)
        elif 'usage' in meta and isinstance(meta['usage'], dict):
            input_tokens = meta['usage'].get('prompt_tokens', meta['usage'].get('input_tokens', 0))
            output_tokens = meta['usage'].get('completion_tokens', meta['usage'].get('output_tokens', 0))
        elif 'usage_metadata' in meta and isinstance(meta['usage_metadata'], dict):
            input_tokens = meta['usage_metadata'].get('prompt_token_count', 0)
            output_tokens = meta['usage_metadata'].get('candidates_token_count', 0)
            
    # Heuristic fallback if still zero
    if input_tokens == 0:
        # Heuristic: sum of characters in messages / 4
        total_chars = 0
        for msg in messages:
            if hasattr(msg, 'content'):
                total_chars += len(str(msg.content))
            elif isinstance(msg, dict):
                total_chars += len(str(msg.get('content', '')))
            else:
                total_chars += len(str(msg))
        input_tokens = max(1, total_chars // 4)
        
    if output_tokens == 0:
        content = ""
        if hasattr(response, 'content'):
            content = response.content
        elif isinstance(response, str):
            content = response
        output_tokens = max(1, len(str(content)) // 4)
        
    return input_tokens, output_tokens
