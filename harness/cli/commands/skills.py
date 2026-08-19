"""
ah skills — Skill management commands.

Commands:
- list: Show installed skills
- add: Add a skill from path or git
- remove: Remove a skill
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from harness.config import ConfigManager
from harness.core.exceptions import BOUNDARY_ERRORS

console = Console()

app = typer.Typer(
    name="skills",
    help="Manage Agent Harness skills.",
    no_args_is_help=True,
)


def _load_external_skill_file(skill_file: Path, skills_dir: Path) -> list[dict]:
    """Load skill metadata from a single Python file; fall back on load errors."""
    try:
        import importlib.util
        import inspect

        from harness.skills.base import BaseSkill

        spec = importlib.util.spec_from_file_location(skill_file.stem, skill_file)
        if not spec or not spec.loader:
            return []

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        found: list[dict] = []
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if obj is BaseSkill:
                continue
            if issubclass(obj, BaseSkill) and hasattr(obj, "name") and obj.name:
                found.append(
                    {
                        "name": obj.name,
                        "description": getattr(obj, "description", ""),
                        "source": str(skills_dir),
                        "file": skill_file.name,
                    }
                )
        return found
    except BOUNDARY_ERRORS:
        return [
            {
                "name": skill_file.stem,
                "description": "[error loading]",
                "source": str(skills_dir),
                "file": skill_file.name,
            }
        ]


def _get_skills_info() -> tuple[list[dict], list[dict]]:
    """
    Get information about all skills.

    :returns: Tuple of (builtin_skills, external_skills) where each is a list of dicts.
    """
    builtin_skills: list[dict] = []
    external_skills: list[dict] = []

    try:
        from harness.skills.builtin import get_builtin_skills

        for skill in get_builtin_skills():
            builtin_skills.append(
                {
                    "name": skill.name,
                    "description": skill.description,
                    "source": "builtin",
                }
            )
    except BOUNDARY_ERRORS as e:
        console.print(f"[yellow]Warning: Could not load builtin skills: {e}[/yellow]")

    manager = ConfigManager()
    skills_dirs = [
        Path("./skills"),
        manager.skills_dir,
        Path.home() / ".harness" / "skills",
    ]

    for skills_dir in skills_dirs:
        if not skills_dir.exists():
            continue

        for skill_file in skills_dir.glob("*.py"):
            if skill_file.name.startswith("_"):
                continue
            external_skills.extend(_load_external_skill_file(skill_file, skills_dir))

    return builtin_skills, external_skills


@app.command(name="list")
def list_command(
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed skill information.",
    ),
) -> None:
    """
    List installed skills.

    Shows builtin and user-installed skills.
    """
    console.print()
    console.print("[bold cyan]Agent Harness — Skills[/bold cyan]")
    console.print()

    builtin_skills, external_skills = _get_skills_info()

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan")
    table.add_column("Name", style="bold", width=16)
    table.add_column("Source", width=12)
    table.add_column("Description", overflow="fold")

    if verbose:
        table.add_column("File", width=20)

    # Builtin skills
    for skill in builtin_skills:
        row = [
            skill["name"],
            "[dim]builtin[/dim]",
            skill["description"][:60] + "..."
            if len(skill.get("description", "")) > 60
            else skill.get("description", ""),
        ]
        if verbose:
            row.append("-")
        table.add_row(*row)

    # External skills
    for skill in external_skills:
        source = skill["source"]
        if "/.agent-harness/" in source:
            source_display = "[cyan]user[/cyan]"
        elif "./skills" in source or source.endswith("/skills"):
            source_display = "[green]project[/green]"
        else:
            source_display = "[dim]external[/dim]"

        row = [
            skill["name"],
            source_display,
            skill["description"][:60] + "..."
            if len(skill.get("description", "")) > 60
            else skill.get("description", ""),
        ]
        if verbose:
            row.append(skill.get("file", "-"))
        table.add_row(*row)

    console.print(table)
    console.print()
    console.print(
        f"[dim]Total: {len(builtin_skills)} builtin, {len(external_skills)} external[/dim]"
    )
    console.print()


@app.command(name="add")
def add_command(
    source: str = typer.Argument(
        ...,
        help="Path to skill file/directory or git URL.",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        "-n",
        help="Custom name for the skill (defaults to file/directory name).",
    ),
    symlink: bool = typer.Option(
        False,
        "--symlink",
        "-s",
        help="Create symlink instead of copying (for local paths).",
    ),
) -> None:
    """
    Add a skill from path or git repository.

    Examples:
        ah skills add /path/to/my_skill.py
        ah skills add /path/to/skill-directory/
        ah skills add git@github.com:user/skill-repo.git
        ah skills add ./local-skill.py --symlink
    """
    manager = ConfigManager()
    target_dir = manager.skills_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    source_path = Path(source)

    # Check if it's a git URL
    is_git = (
        source.startswith("git@") or source.endswith(".git") or "github.com" in source
    )

    if is_git:
        # Clone from git
        repo_name = name or source.split("/")[-1].replace(".git", "")
        target_path = target_dir / repo_name

        if target_path.exists():
            console.print(f"[yellow]Directory already exists: {target_path}[/yellow]")
            if not typer.confirm("Overwrite?", default=False):
                raise typer.Exit(0)
            shutil.rmtree(target_path)

        console.print(f"[dim]Cloning {source}...[/dim]")
        try:
            subprocess.run(
                ["git", "clone", source, str(target_path)],
                check=True,
                capture_output=True,
            )
            console.print(f"[green]Added skill from git: {repo_name}[/green]")
            console.print(f"[dim]Location: {target_path}[/dim]")
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Git clone failed: {e.stderr.decode()}[/red]")
            raise typer.Exit(1)
        except FileNotFoundError:
            console.print(
                "[red]Git not found. Install git to clone repositories.[/red]"
            )
            raise typer.Exit(1)

    elif source_path.exists():
        # Copy or symlink local file/directory
        skill_name = name or source_path.stem

        if source_path.is_file():
            target_path = target_dir / f"{skill_name}.py"
        else:
            target_path = target_dir / skill_name

        if target_path.exists():
            console.print(f"[yellow]Already exists: {target_path}[/yellow]")
            if not typer.confirm("Overwrite?", default=False):
                raise typer.Exit(0)
            if target_path.is_dir():
                shutil.rmtree(target_path)
            else:
                target_path.unlink()

        if symlink:
            # Create symlink
            target_path.symlink_to(source_path.resolve())
            console.print(f"[green]Created symlink: {skill_name}[/green]")
        else:
            # Copy
            if source_path.is_file():
                shutil.copy2(source_path, target_path)
            else:
                shutil.copytree(source_path, target_path)
            console.print(f"[green]Added skill: {skill_name}[/green]")

        console.print(f"[dim]Location: {target_path}[/dim]")

    else:
        console.print(f"[red]Source not found: {source}[/red]")
        raise typer.Exit(1)


@app.command(name="remove")
def remove_command(
    name: str = typer.Argument(
        ...,
        help="Name of the skill to remove.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Remove without confirmation.",
    ),
) -> None:
    """
    Remove an installed skill.

    Only user-installed skills can be removed. Builtin skills cannot be removed.
    """
    # Check if it's a builtin skill
    builtin_skills, _ = _get_skills_info()
    builtin_names = {s["name"] for s in builtin_skills}

    if name in builtin_names:
        console.print(f"[red]Cannot remove builtin skill: {name}[/red]")
        raise typer.Exit(1)

    # Find the skill
    manager = ConfigManager()
    skills_dirs = [
        manager.skills_dir,
        Path("./skills"),
        Path.home() / ".harness" / "skills",
    ]

    found_path = None
    for skills_dir in skills_dirs:
        # Check for file
        skill_file = skills_dir / f"{name}.py"
        if skill_file.exists():
            found_path = skill_file
            break

        # Check for directory
        skill_dir = skills_dir / name
        if skill_dir.exists():
            found_path = skill_dir
            break

    if not found_path:
        console.print(f"[red]Skill not found: {name}[/red]")
        console.print("Use [cyan]ah skills list[/cyan] to see installed skills.")
        raise typer.Exit(1)

    # Confirm
    if not force:
        console.print(f"[yellow]Will remove: {found_path}[/yellow]")
        if not typer.confirm("Continue?", default=False):
            console.print("[dim]Cancelled.[/dim]")
            raise typer.Exit(0)

    # Remove
    if found_path.is_dir():
        shutil.rmtree(found_path)
    else:
        found_path.unlink()

    console.print(f"[green]Removed skill: {name}[/green]")
