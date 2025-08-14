import os
import json
import shutil
import time
import argparse
import sys
import zipfile
import glob
import subprocess
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch

# --- Dependency Check and Installation ---
# This block MUST come before importing any of the packages below.
REQUIRED_PACKAGES = ['requests', 'questionary', 'rich']
for package in REQUIRED_PACKAGES:
    try:
        __import__(package)
    except ImportError:
        print(f"'{package}' not found. Attempting to install...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])
        except Exception as e:
            print(f"FATAL: Could not install required package '{package}'. Please install it manually.")
            print(f"Error: {e}")
            sys.exit(1)

# --- Third-Party Imports ---
# Now that the packages are guaranteed to be installed, we can import them.
import requests
import questionary
from questionary import Style
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# --- Script Version ---
VERSION = "1.0.0"  # Updated for Clear Cache menu option

# --- Rich Console ---
console = Console()

# --- Custom Questionary Style ---
custom_style = Style([
    ('qmark', 'fg:#00ffff bold'),
    ('question', 'fg:#ffffff bold'),
    ('answer', 'fg:#44ff00 bold'),
    ('pointer', 'fg:#ff6600 bold'),
    ('highlighted', 'fg:#ff6600 bold'),
    ('selected', 'fg:#00ff88'),
    ('separator', 'fg:#00ffff bold'),
    ('instruction', 'fg:#858585 italic'),
    ('text', 'fg:#ffffff'),
    ('disabled', 'fg:#858585 italic')
])

# --- Configuration ---
COMPONENTS_FILE = 'components.json'
SKELETON_FILE = 'skeleton.zip'
CACHE_FILE = 'hats_pack_cache.json'
DOWNLOAD_DIR = 'temp_downloads'
BUILD_DIR = 'build'
OUTPUT_FILENAME = 'HATS_Pack_Custom.zip'
SUMMARY_FILENAME = 'HATS_Pack_Contents.txt'
CACHE_DURATION = timedelta(hours=12)

# --- Global PAT Storage ---
github_pat = None  # Store PAT in memory for the session

# --- Argument Parser ---
parser = argparse.ArgumentParser(description=f"HATS Pack Builder v{VERSION}")
parser.add_argument("--clear-cache", action="store_true", help="Clear the cache to force API refresh")
args = parser.parse_args()

# --- Path Handling ---
def get_base_path():
    if getattr(sys, 'frozen', False):  # Check if running as an executable
        return os.path.dirname(sys.executable)  # Use directory of the .exe file
    return os.path.dirname(os.path.abspath(__file__))  # Use directory of the .py file

# --- Caching & API Functions ---
def load_cache():
    cache_path = os.path.join(get_base_path(), CACHE_FILE)
    if not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}

def save_cache(cache):
    cache_path = os.path.join(get_base_path(), CACHE_FILE)
    try:
        with open(cache_path, 'w') as f:
            json.dump(cache, f, indent=4)
    except IOError as e:
        console.print(f"  > [yellow]WARNING:[/] Could not save cache file: {e}")

def clear_cache():
    cache_path = os.path.join(get_base_path(), CACHE_FILE)
    if os.path.exists(cache_path):
        try:
            os.remove(cache_path)
            console.print(f"[yellow]Cache file '{CACHE_FILE}' cleared. Next builder run will fetch live data.[/]")
        except OSError as e:
            console.print(f"[bold red]ERROR:[/] Could not clear cache file: {e}")
    else:
        console.print(f"[yellow]No cache file found ('{CACHE_FILE}').[/]")
    questionary.press_any_key_to_continue("Press any key to return to the main menu...", style=custom_style).ask()

def handle_rate_limit(response, repo):
    if response.status_code in (403, 429) and "x-ratelimit-remaining" in response.headers and response.headers["x-ratelimit-remaining"] == "0":
        reset_time = int(response.headers.get("x-ratelimit-reset", 0))
        wait_time = max(reset_time - int(time.time()), 1)
        console.print(f"\n  > [bold yellow]WARNING:[/] GitHub API rate limit exceeded for {repo}.")
        console.print(f"    Please wait {wait_time} seconds to continue.")
        time.sleep(wait_time + 1)
        return True
    return False

def get_release_asset_info(component, token, cache):
    repo = component.get('repo')
    asset_pattern = component.get('asset_pattern')
    tag = component.get('tag')
    cache_key = f"{repo}@{tag or 'latest'}|{asset_pattern}"
    current_time = datetime.now(timezone.utc)

    if not args.clear_cache and cache_key in cache:
        cache_entry = cache[cache_key]
        try:
            cache_time = datetime.fromisoformat(cache_entry["timestamp"])
            if cache_time.tzinfo is None:
                cache_time = cache_time.replace(tzinfo=timezone.utc)
            if current_time - cache_time < CACHE_DURATION:
                return cache_entry
        except (ValueError, TypeError):
            console.print(f"[yellow]WARNING:[/] Invalid cache timestamp for {cache_key}. Fetching fresh data.")
            pass
    
    if tag:
        api_url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    else:
        api_url = f"https://api.github.com/repos/{repo}/releases"
    
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    if cache_key in cache and "etag" in cache[cache_key]:
        headers["If-None-Match"] = cache[cache_key]["etag"]

    try:
        response = requests.get(api_url, headers=headers)
        if response.status_code == 304:
            return cache[cache_key]
        
        if handle_rate_limit(response, repo):
            return get_release_asset_info(component, token, cache)

        response.raise_for_status()
        release_data = response.json()

        if not tag:
            if not release_data:
                return None
            release_data = release_data[0]

        for asset in release_data.get('assets', []):
            if fnmatch(asset['name'], asset_pattern):
                asset_info = {
                    "url": asset['browser_download_url'],
                    "version": release_data.get('tag_name', 'N/A'),
                    "timestamp": current_time.isoformat(),
                    "etag": response.headers.get("ETag", "")
                }
                cache[cache_key] = asset_info
                return asset_info
        return None
    except requests.exceptions.RequestException:
        return None

def download_file(url, download_path, token=None):
    headers = {}
    if token and "github.com" in url:
        headers["Authorization"] = f"token {token}"
        headers["Accept"] = "application/octet-stream"
    try:
        with requests.get(url, headers=headers, stream=True) as r:
            r.raise_for_status()
            with open(download_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return True
    except requests.exceptions.RequestException as e:
        console.print(f"  > [bold red]ERROR:[/] Failed to download {url}. {e}")
        return False

# --- HATS Processing Logic ---
def process_component(component, downloaded_file_path, build_dir):
    console.print(f"  -> [cyan]Processing {component['name']}...[/]")
    for step in component.get('processing_steps', []):
        action = step.get('action')
        try:
            if action == 'unzip_to_root':
                with zipfile.ZipFile(downloaded_file_path, 'r') as zf:
                    zf.extractall(build_dir)
                console.print(f"     - Unzipped to build directory root.")
            elif action == 'copy_file':
                target_path_str = step['target_path'].strip('/\\')
                target_path = os.path.join(build_dir, target_path_str)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                shutil.copy(downloaded_file_path, target_path)
                console.print(f"     - Copied file to {step['target_path']}")
            elif action == 'unzip_folder':
                target_dir = os.path.join(build_dir, step['target_path'].strip('/\\'))
                os.makedirs(target_dir, exist_ok=True)
                with zipfile.ZipFile(downloaded_file_path, 'r') as zf:
                    zf.extractall(target_dir)
                console.print(f"     - Extracted contents to '{step['target_path']}'")
            elif action == 'find_and_copy':
                source_pattern = step['source_file_pattern']
                target_dir = os.path.join(build_dir, step['target_path'].strip('/\\'))
                os.makedirs(target_dir, exist_ok=True)
                with zipfile.ZipFile(downloaded_file_path, 'r') as zf:
                    for member in zf.infolist():
                        if fnmatch(os.path.basename(member.filename), source_pattern) and not member.is_dir():
                            member_filename = os.path.basename(member.filename)
                            target_path = os.path.join(target_dir, member_filename)
                            with open(target_path, "wb") as f:
                                f.write(zf.read(member.filename))
                            console.print(f"     - Found and copied '{member_filename}' to '{step['target_path']}'")
                            break
            elif action == 'find_and_rename':
                source_pattern = step['source_file_pattern']
                target_filename = step['target_filename']
                target_dir = os.path.join(build_dir, step['target_path'].strip('/\\'))
                os.makedirs(target_dir, exist_ok=True)
                target_path = os.path.join(target_dir, target_filename)
                with zipfile.ZipFile(downloaded_file_path, 'r') as zf:
                    for member in zf.infolist():
                        if fnmatch(os.path.basename(member.filename), source_pattern):
                            with open(target_path, 'wb') as f:
                                f.write(zf.read(member.filename))
                            console.print(f"     - Found and renamed '{os.path.basename(member.filename)}' to '{target_filename}'")
                            break
            elif action == 'delete_file':
                path_pattern = os.path.join(build_dir, step['path'].strip('/\\'))
                files_to_delete = glob.glob(path_pattern)
                for f in files_to_delete:
                    if os.path.isfile(f):
                        os.remove(f)
                        console.print(f"     - Deleted file: {os.path.basename(f)}")
        except Exception as e:
            console.print(f"     - [bold red]ERROR[/] processing step '{action}': {e}")

def create_final_zip(build_dir, output_filename):
    console.print("\n[bold]Creating final ZIP file...[/]")
    shutil.make_archive(output_filename.replace('.zip', ''), 'zip', build_dir)
    console.print(f"[bold green]Successfully created {output_filename}[/]")

def create_pack_summary(user_choices, categories, output_path, script_version):
    base_path = get_base_path()  # Use the updated get_base_path()
    summary_path = os.path.join(base_path, SUMMARY_FILENAME)  # Save in the same directory as .exe or .py
    wib_time = datetime.now(timezone.utc) + timedelta(hours=7)
    
    content = []
    content.append("===================================")
    content.append(" HATS Pack - Custom Build Contents ")
    content.append("===================================")
    content.append(f"\nGenerated on: {wib_time.strftime('%Y-%m-%d %H:%M:%S WIB')}")
    content.append(f"Builder Version: {script_version}\n")
    
    for category in categories:
        selections_in_category = {k: v for k, v in user_choices.items() if v['category'] == category}
        if selections_in_category:
            content.append(f"--- {category.upper()} ---")
            for comp in selections_in_category.values():
                version = comp.get('asset_info', {}).get('version', 'N/A')
                content.append(f"  - {comp['name']} ({version})")
            content.append("")
            
    try:
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(content))
        console.print(f"[bold green]Successfully created summary file: {SUMMARY_FILENAME}[/]")
    except IOError as e:
        console.print(f"[bold red]ERROR:[/] Could not create summary file: {e}")

# --- JSON Editor Functions ---
def load_components():
    components_path = os.path.join(get_base_path(), COMPONENTS_FILE)
    if not os.path.exists(components_path):
        console.print(f"[yellow]Warning:[/] '{COMPONENTS_FILE}' not found. Starting with an empty list.")
        return {}
    try:
        with open(components_path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        console.print(f"[bold red]Error:[/] Could not read or parse '{COMPONENTS_FILE}': {e}")
        return None

def save_components(components):
    components_path = os.path.join(get_base_path(), COMPONENTS_FILE)
    backup_file = components_path + '.bak'
    try:
        if os.path.exists(components_path):
            shutil.copy(components_path, backup_file)
        with open(components_path, 'w') as f:
            json.dump(components, f, indent=2, sort_keys=True)
        console.print(f"[green]Successfully saved changes to '{COMPONENTS_FILE}'![/]")
        console.print(f"[dim]Backup created at '{backup_file}'[/dim]")
        return True
    except IOError as e:
        console.print(f"[bold red]Error:[/] Could not save file: {e}")
        return False

def view_components(components):
    if not components:
        console.print("[yellow]No components to display.[/]")
        return
    table = Table(title="[bold blue]HATS Pack Components[/]")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="magenta")
    table.add_column("Category", style="green")
    table.add_column("Repo/URL", style="yellow")
    for comp_id, details in sorted(components.items()):
        repo_or_url = details.get('repo', details.get('url', 'N/A'))
        table.add_row(comp_id, details.get('name', 'N/A'), details.get('category', 'N/A'), repo_or_url)
    console.print(table)

def get_processing_step():
    action = questionary.select(
        "Action:",
        choices=["copy_file", "unzip_to_root", "unzip_to_folder", "find_and_copy", "find_and_rename", "delete_file"],
        style=custom_style
    ).ask()
    step = {"action": action}
    if "path" in action or "folder" in action or "rename" in action:
        step['target_path'] = questionary.text("Target Path (e.g., '/switch/AppName.nro'):", style=custom_style).ask()
    if "pattern" in action or "find" in action:
        step['source_file_pattern'] = questionary.text("Source File Pattern (e.g., '*.nro'):", style=custom_style).ask()
    if "rename" in action:
        step['target_filename'] = questionary.text("Target Filename (e.g., 'payload.bin'):", style=custom_style).ask()
    return step

def edit_processing_step(step):
    edited_step = step.copy()
    actions = ["copy_file", "unzip_to_root", "unzip_to_folder", "find_and_copy", "find_and_rename", "delete_file"]
    edited_step['action'] = questionary.select(
        "Action:",
        choices=actions,
        default=edited_step.get('action', 'copy_file'),
        style=custom_style
    ).ask()
    if edited_step['action'] in ["copy_file", "unzip_to_folder", "find_and_copy", "find_and_rename"]:
        edited_step['target_path'] = questionary.text(
            "Target Path:",
            default=edited_step.get('target_path', ''),
            style=custom_style
        ).ask()
    if edited_step['action'] in ["find_and_copy", "find_and_rename"]:
        edited_step['source_file_pattern'] = questionary.text(
            "Source File Pattern:",
            default=edited_step.get('source_file_pattern', ''),
            style=custom_style
        ).ask()
    if edited_step['action'] == "find_and_rename":
        edited_step['target_filename'] = questionary.text(
            "Target Filename:",
            default=edited_step.get('target_filename', ''),
            style=custom_style
        ).ask()
    final_step = {'action': edited_step['action']}
    if edited_step.get('target_path'):
        final_step['target_path'] = edited_step['target_path']
    if edited_step.get('source_file_pattern'):
        final_step['source_file_pattern'] = edited_step['source_file_pattern']
    if edited_step.get('target_filename'):
        final_step['target_filename'] = edited_step['target_filename']
    return final_step

def add_component(components):
    console.print(Panel("[bold white]Add New Component Wizard[/]", style="bold green"))
    new_id = questionary.text("Enter a unique ID (e.g., 'new_app'):", style=custom_style).ask()
    if not new_id or new_id in components:
        console.print("[bold red]Error:[/] ID cannot be empty and must be unique.")
        return components
    new_comp = {}
    new_comp['name'] = questionary.text("Name (e.g., 'New App'):", style=custom_style).ask()
    new_comp['description'] = questionary.text("Description:", style=custom_style).ask()
    new_comp['category'] = questionary.select("Category:", choices=["Essential", "Homebrew Apps", "Patches", "Tesla Overlays", "Payloads"], style=custom_style).ask()
    new_comp['default'] = questionary.confirm("Selected by default?", style=custom_style).ask()
    new_comp['source_type'] = questionary.select("Source Type:", choices=["github_release", "direct_url"], style=custom_style).ask()
    if new_comp['source_type'] == 'github_release':
        new_comp['repo'] = questionary.text("GitHub Repo (user/repo):", style=custom_style).ask()
        if questionary.confirm("Does this use a specific release tag (not 'latest')?", style=custom_style).ask():
            new_comp['tag'] = questionary.text("Enter the tag:", style=custom_style).ask()
    else:
        new_comp['url'] = questionary.text("Direct Download URL:", style=custom_style).ask()
    new_comp['asset_pattern'] = questionary.text("Asset Pattern (e.g., 'AppName-*.zip'):", style=custom_style).ask()
    steps = []
    while questionary.confirm("Add a processing step?", style=custom_style).ask():
        steps.append(get_processing_step())
    new_comp['processing_steps'] = steps
    summary_table = Table(title=f"[bold yellow]Review New Component: {new_id}[/]")
    summary_table.add_column("Field", style="cyan")
    summary_table.add_column("Value", style="white")
    for key, value in new_comp.items():
        summary_table.add_row(key, str(value))
    console.print(summary_table)
    if questionary.confirm("Does this look correct? Save this new component?", style=custom_style).ask():
        components[new_id] = new_comp
        console.print(f"\n[green]Component '{new_comp['name']}' added![/]")
    else:
        console.print("\n[yellow]Cancelled. Component was not added.[/]")
    return components

def edit_component(components):
    if not components:
        console.print("[yellow]No components to edit.[/]")
        return components
    choices = [questionary.Choice(f"{details['name']} ({comp_id})", value=comp_id) for comp_id, details in sorted(components.items())]
    comp_id_to_edit = questionary.select("Which component do you want to edit?", choices=choices, style=custom_style).ask()
    if not comp_id_to_edit:
        return components
    comp = components[comp_id_to_edit].copy()
    original_comp = components[comp_id_to_edit]
    console.print(f"[bold]--- Editing '{comp['name']}' ---[/]")
    console.print("[dim](Press Enter to keep the current value)[/dim]")
    for key in ['name', 'description', 'category', 'asset_pattern', 'repo', 'tag', 'url']:
        if key in comp:
            new_val = questionary.text(f"Enter new value for '{key}':", default=str(comp[key]), style=custom_style).ask()
            comp[key] = new_val
    if questionary.confirm("Do you want to edit the processing steps?", style=custom_style).ask():
        steps = comp.get('processing_steps', [])
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            console.print(Panel("[bold white]Processing Steps Editor[/]", style="bold cyan", subtitle=f"for {comp['name']}"))
            if not steps:
                console.print("[yellow]No processing steps currently defined.[/]")
            else:
                for i, step in enumerate(steps):
                    console.print(f"  [bold cyan]{i+1}:[/] {step}")
            action = questionary.select(
                "What would you like to do?",
                choices=["Add a new step", "Edit an existing step", "Delete a step", "Finish editing steps"],
                style=custom_style
            ).ask()
            if action == "Add a new step":
                steps.append(get_processing_step())
            elif action == "Edit an existing step":
                if not steps:
                    continue
                step_index_str = questionary.text(f"Enter the number of the step to edit (1-{len(steps)}):", style=custom_style).ask()
                try:
                    step_index = int(step_index_str) - 1
                    if 0 <= step_index < len(steps):
                        console.print(f"Editing step {step_index+1}: {steps[step_index]}")
                        steps[step_index] = edit_processing_step(steps[step_index])
                    else:
                        console.print("[red]Invalid number.[/]")
                except (ValueError, TypeError):
                    console.print("[red]Invalid input.[/]")
            elif action == "Delete a step":
                if not steps:
                    continue
                step_index_str = questionary.text(f"Enter the number of the step to delete (1-{len(steps)}):", style=custom_style).ask()
                try:
                    step_index = int(step_index_str) - 1
                    if 0 <= step_index < len(steps):
                        deleted = steps.pop(step_index)
                        console.print(f"Deleted step: {deleted}")
                    else:
                        console.print("[red]Invalid number.[/]")
                except (ValueError, TypeError):
                    console.print("[red]Invalid input.[/]")
            elif action == "Finish editing steps" or action is None:
                break
        comp['processing_steps'] = steps
    summary_table = Table(title=f"[bold yellow]Review Changes for: {comp_id_to_edit}[/]")
    summary_table.add_column("Field", style="cyan")
    summary_table.add_column("Old Value", style="red")
    summary_table.add_column("New Value", style="green")
    all_keys = set(original_comp.keys()) | set(comp.keys())
    for key in sorted(list(all_keys)):
        old_val = str(original_comp.get(key, ''))
        new_val = str(comp.get(key, ''))
        if old_val != new_val:
            summary_table.add_row(key, old_val, f"[bold]{new_val}[/]")
        else:
            summary_table.add_row(key, old_val, new_val)
    console.print(summary_table)
    if questionary.confirm("Save these changes?", style=custom_style).ask():
        components[comp_id_to_edit] = comp
        console.print(f"\n[green]Component '{comp['name']}' updated![/]")
    else:
        console.print("\n[yellow]Cancelled. No changes were made.[/]")
    return components

def delete_component(components):
    if not components:
        console.print("[yellow]No components to delete.[/]")
        return components
    choices = [questionary.Choice(f"{details['name']} ({comp_id})", value=comp_id) for comp_id, details in sorted(components.items())]
    comp_id_to_delete = questionary.select("Which component do you want to delete?", choices=choices, style=custom_style).ask()
    if not comp_id_to_delete:
        return components
    comp_name = components[comp_id_to_delete]['name']
    if questionary.confirm(f"Are you sure you want to delete '{comp_name}'? This cannot be undone.", style=custom_style).ask():
        del components[comp_id_to_delete]
        console.print(f"[green]Component '{comp_name}' deleted.[/]")
    else:
        console.print("Deletion cancelled.")
    return components

def edit_components_menu():
    components = load_components()
    if components is None:
        return
    while True:
        os.system('cls' if os.name == 'nt' else 'clear')
        console.print(Panel(f"[bold white]Component Editor[/]", 
                           style="bold magenta", subtitle="Manage your components.json", 
                           subtitle_align="right"))
        choice = questionary.select(
            "What would you like to do?",
            choices=[
                "View All Components",
                "Add a New Component",
                "Edit an Existing Component",
                "Delete a Component",
                questionary.Separator(),
                "Save and Return to Main Menu",
                "Return to Main Menu Without Saving"
            ],
            style=custom_style
        ).ask()
        if choice == "View All Components":
            view_components(components)
            questionary.press_any_key_to_continue(style=custom_style).ask()
        elif choice == "Add a New Component":
            components = add_component(components)
            questionary.press_any_key_to_continue(style=custom_style).ask()
        elif choice == "Edit an Existing Component":
            components = edit_component(components)
            questionary.press_any_key_to_continue(style=custom_style).ask()
        elif choice == "Delete a Component":
            components = delete_component(components)
            questionary.press_any_key_to_continue(style=custom_style).ask()
        elif choice == "Save and Return to Main Menu":
            save_components(components)
            return
        elif choice == "Return to Main Menu Without Saving" or choice is None:
            return

def run_builder():
    global github_pat  # Access the global PAT variable
    base_path = get_base_path()
    components_path = os.path.join(base_path, COMPONENTS_FILE)
    skeleton_path = os.path.join(base_path, SKELETON_FILE)
    output_path = os.path.join(base_path, OUTPUT_FILENAME)
    temp_download_path = os.path.join(base_path, DOWNLOAD_DIR)
    temp_build_path = os.path.join(base_path, BUILD_DIR)

    while True:  # Loop to allow restarting the builder
        # Clear the screen and display the HATS Builder title
        os.system('cls' if os.name == 'nt' else 'clear')
        console.print(Panel(f"[bold white]HATS Pack Builder[/]", 
                           style="bold blue", subtitle="Build your own HATS pack", 
                           subtitle_align="right"))

        if args.clear_cache and os.path.exists(os.path.join(base_path, CACHE_FILE)):
            os.remove(os.path.join(base_path, CACHE_FILE))
            console.print("[yellow]Cache file cleared.[/]")

        # Prompt for GitHub PAT only if not already set
        if github_pat is None:
            pat_input = questionary.password(
                "Enter GitHub PAT to avoid rate limits (optional, press Enter to skip or type 'back' to return to main menu):",
                qmark="🔑",
                style=custom_style
            ).ask()
            if pat_input == 'back':
                return
            github_pat = pat_input if pat_input else None

        all_components = load_components()
        if all_components is None:
            return

        cache = load_cache()
        with console.status("[bold green]Fetching latest component information...") as status:
            total_components = len(all_components)
            for i, (component_id, component) in enumerate(all_components.items()):
                percent_done = int(((i + 1) / total_components) * 100)
                status.update(f"[bold green]Fetching info... {percent_done}% - {component['name']}[/]")
                if component.get('source_type') == 'github_release':
                    asset_info = get_release_asset_info(component, github_pat, cache)
                    if asset_info:
                        all_components[component_id]['asset_info'] = asset_info
        save_cache(cache)
        console.print("✅ [bold green]Information updated.[/]")

        categories = sorted(list(set(all_components[c]['category'] for c in all_components)))
        choices = []
        for category in categories:
            choices.append(questionary.Separator(f"--- {category.upper()} ---"))
            components_in_category = {k: v for k, v in all_components.items() if v['category'] == category}
            for id, comp in sorted(components_in_category.items()):
                version = comp.get('asset_info', {}).get('version', 'N/A')
                title = f"{comp['name']} ({version})"
                choices.append(questionary.Choice(title=title, value=id, checked=comp.get('default', False)))
            choices.append(questionary.Separator(" "))
        choices.append(questionary.Separator("--- OPTIONS ---"))
        choices.append(questionary.Choice(title="Return to Main Menu", value="return_to_main"))

        selected_ids = questionary.checkbox(
            "Select the components you want to include:",
            choices=choices,
            style=custom_style,
            instruction="(Use arrow keys to move, <space> to select, <a> to toggle all, <i> to invert, <Enter> to confirm)",
            validate=lambda selections: (
                True if selections or "return_to_main" in selections
                else "You must select at least one component or choose 'Return to Main Menu'."
            )
        ).ask()

        if selected_ids is None or "return_to_main" in selected_ids:
            console.print("[yellow]Returning to main menu.[/]")
            return

        user_choices = {id: all_components[id] for id in selected_ids}

        os.system('cls' if os.name == 'nt' else 'clear')
        summary_table = Table(title="[bold green]Your Custom HATS Pack[/]")
        summary_table.add_column("Category", style="blue")
        summary_table.add_column("Component", style="magenta")
        summary_table.add_column("Version", style="cyan")

        for category in categories:
            selections_in_category = [id for id in selected_ids if all_components[id]['category'] == category]
            if selections_in_category:
                for i, id in enumerate(selections_in_category):
                    comp = all_components[id]
                    version = comp.get('asset_info', {}).get('version', 'N/A')
                    category_name = category if i == 0 else ""
                    summary_table.add_row(category_name, comp['name'], version)

        console.print(summary_table)
        confirm_choice = questionary.select(
            "What would you like to do?",
            choices=[
                "Proceed with this selection",
                "Return to HATS Builder"
            ],
            style=custom_style
        ).ask()
        if confirm_choice == "Return to HATS Builder":
            console.print("[yellow]Returning to HATS Builder.[/]")
            continue  # Restart the builder loop
        elif confirm_choice != "Proceed with this selection":
            console.print("[yellow]Returning to main menu.[/]")
            return

        if os.path.exists(temp_download_path):
            shutil.rmtree(temp_download_path)
        if os.path.exists(temp_build_path):
            shutil.rmtree(temp_build_path)
        os.makedirs(temp_download_path)
        os.makedirs(temp_build_path)

        try:
            console.print("\n[bold]Starting build process...[/]")
            console.print(f"-> [cyan]Processing skeleton file:[/] {SKELETON_FILE}")
            with zipfile.ZipFile(skeleton_path, 'r') as zf:
                zf.extractall(temp_build_path)
            console.print("  > [green]Skeleton extracted successfully.[/]")
        except FileNotFoundError:
            console.print(f"[bold red]ERROR:[/] '{SKELETON_FILE}' not found.")
            return

        i = 0
        for component_id, component in user_choices.items():
            i += 1
            console.print(f"\n-> [bold][{i}/{len(user_choices)}][/] [bold]Processing:[/] {component['name']}")
            asset_info = component.get("asset_info")
            if asset_info and asset_info.get("url"):
                download_url = asset_info["url"]
                console.print(f"  > [dim]Version:[/] {asset_info['version']}")
                filename = component_id + '_' + os.path.basename(download_url).split('?')[0]
                download_path = os.path.join(temp_download_path, filename)
                console.print(f"  > [dim]Downloading from:[/] {download_url.split('?')[0]}")
                if download_file(download_url, download_path, github_pat):
                    process_component(component, download_path, temp_build_path)
            else:
                console.print(f"  > [yellow]Skipping component as no version/URL info was found.[/]")

        create_final_zip(temp_build_path, output_path)
        create_pack_summary(user_choices, categories, output_path, VERSION)
        
        if os.path.exists(temp_download_path):
            shutil.rmtree(temp_download_path)
        if os.path.exists(temp_build_path):
            shutil.rmtree(temp_build_path)
        console.print(Panel("[bold green]Build Complete! 🥳[/]", subtitle=f"Find your file at: {output_path}"))
        questionary.press_any_key_to_continue("Press any key to return to the main menu...", style=custom_style).ask()
        return  # Exit the builder after successful build

def reset_pat():
    global github_pat
    github_pat = None
    console.print("[yellow]GitHub PAT has been cleared. You will be prompted to enter a new one next time you use the builder.[/]")
    questionary.press_any_key_to_continue("Press any key to return to the main menu...", style=custom_style).ask()

# --- Main Menu ---
def main():
    while True:
        os.system('cls' if os.name == 'nt' else 'clear')
        console.print(Panel(f"[bold white]HATSKit v{VERSION}[/]", 
                           style="bold blue", subtitle="Build or manage your HATS pack", 
                           subtitle_align="right"))
        choices = [
            "Load the HATS Pack Builder",
            "Load the Component Editor",
            "Clear Cache",
            questionary.Separator()
        ]
        if github_pat is not None:
            choices.append("Clear GitHub PAT")
        choices.append("Exit")
        choice = questionary.select(
            "What would you like to do?",
            choices=choices,
            style=custom_style
        ).ask()
        if choice == "Load the HATS Pack Builder":
            os.system('cls' if os.name == 'nt' else 'clear')
            console.print(Panel(f"[bold white]HATS Pack Builder v{VERSION}[/]", 
                               style="bold blue", subtitle="Build your own HATS pack", 
                               subtitle_align="right"))
            run_builder()
        elif choice == "Load the Component Editor":
            edit_components_menu()
        elif choice == "Clear Cache":
            clear_cache()
        elif choice == "Clear GitHub PAT":
            reset_pat()
        elif choice == "Exit" or choice is None:
            break

if __name__ == '__main__':
    main()