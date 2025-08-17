import os
import json
import shutil
import time
import argparse
import sys
import zipfile
import glob
import subprocess
import hashlib
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
import copy

# --- Dependency Check and Installation ---
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
import requests
import questionary
from questionary import Style
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# --- Script Version ---
VERSION = "1.0.3"

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
CONFIG_FILE = 'config.json'
CACHE_FILE = 'hatskit_cache.json'
LAST_BUILD_FILE = 'last_build.json'
DOWNLOAD_DIR = 'temp_downloads'
BUILD_DIR = 'build'
OUTPUT_FILENAME_BASE = 'HATS'
CACHE_DURATION = timedelta(hours=12)

# --- Global Variables ---
github_pat = None
config = None

# --- Argument Parser ---
parser = argparse.ArgumentParser(description=f"HATSKit v{VERSION}")
parser.add_argument("--clear-cache", action="store_true", help="Clear the cache to force API refresh")
args = parser.parse_args()

# --- Language and Config Handling ---
translations = {}

def load_language(lang_code):
    global translations
    lang_file = os.path.join(get_base_path(), 'languages', f'{lang_code}.json')
    if not os.path.exists(lang_file):
        console.print(f"[yellow]Warning: Language file for '{lang_code}' not found. Falling back to English.[/]")
        lang_file = os.path.join(get_base_path(), 'languages', 'en.json')
    try:
        with open(lang_file, 'r', encoding='utf-8') as f:
            translations = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        console.print(f"[bold red]Error:[/] Could not load language file: {e}")
        translations = {}
    return translations

def get_text(key, **kwargs):
    text = translations.get(key, key)
    try:
        return text.format(**kwargs)
    except KeyError:
        return text

def load_config():
    config_path = os.path.join(get_base_path(), CONFIG_FILE)
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {'language': 'en'}
    return {'language': 'en'}

def save_config(config):
    config_path = os.path.join(get_base_path(), CONFIG_FILE)
    try:
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=4)
    except IOError as e:
        console.print(f"[yellow]WARNING:[/] Could not save config file: {e}")

def get_available_languages():
    lang_dir = os.path.join(get_base_path(), 'languages')
    os.makedirs(lang_dir, exist_ok=True)
    return [f.replace('.json', '') for f in os.listdir(lang_dir) if f.endswith('.json')]

def change_language():
    global config
    available_languages = get_available_languages()
    if not available_languages:
        console.print(f"[yellow]{get_text('error_no_languages')}[/]")
        config['language'] = 'en'
        save_config(config)
        load_language('en')
        questionary.press_any_key_to_continue(get_text("press_any_key"), style=custom_style).ask()
        return
    language_names = {
        'de': 'Deutsch', 'en': 'English', 'es': 'Español', 'fr': 'Français',
        'id': 'Bahasa Indonesia', 'it': 'Italiano', 'ja': '日本語', 'ko': '한국어',
        'pt': 'Português', 'ru': 'Русский', 'zh': '中文'
    }
    lang_code = questionary.select(
        get_text('main_menu_change_language'),
        choices=[questionary.Choice(title=language_names.get(lang, lang.capitalize()), value=lang) for lang in available_languages],
        style=custom_style,
        instruction=get_text('menu_instruction')
    ).ask()
    if lang_code:
        config['language'] = lang_code
        save_config(config)
        load_language(lang_code)
        console.print(f"[green]Language changed to {language_names.get(lang_code, lang_code.capitalize())}.[/]")
    questionary.press_any_key_to_continue(get_text("press_any_key"), style=custom_style).ask()

# --- Component Description Handling ---
def get_component_description(component, lang_code):
    descriptions = component.get('descriptions', {})
    return descriptions.get(lang_code, descriptions.get('en', component.get('description', 'No description')))

# --- Path Handling ---
def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

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

def load_last_build():
    last_build_path = os.path.join(get_base_path(), LAST_BUILD_FILE)
    if not os.path.exists(last_build_path):
        return {}
    try:
        with open(last_build_path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}

def save_last_build(build_info):
    last_build_path = os.path.join(get_base_path(), LAST_BUILD_FILE)
    try:
        with open(last_build_path, 'w') as f:
            json.dump(build_info, f, indent=4)
    except IOError as e:
        console.print(f"  > [yellow]WARNING:[/] Could not save last build info: {e}")

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
    questionary.press_any_key_to_continue(get_text("press_any_key"), style=custom_style).ask()

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

# --- Versioning Functions ---
def compute_content_hash(user_choices):
    hasher = hashlib.sha1()
    for comp_id in sorted(user_choices.keys()):
        comp = user_choices[comp_id]
        version = comp.get('asset_info', {}).get('version', 'N/A')
        hasher.update(f"{comp_id}:{version}".encode('utf-8'))
    return hasher.hexdigest()[:7]

# --- HATS Processing Logic ---
def process_component(component, downloaded_file_path, build_dir):
    console.print(f"  -> [cyan]{get_text('processing_component', name=component['name'])}[/]")
    for step in component.get('processing_steps', []):
        action = step.get('action')
        try:
            if action == 'unzip_to_root':
                with zipfile.ZipFile(downloaded_file_path, 'r') as zf:
                    zf.extractall(build_dir)
                console.print(f"     - {get_text('unzip_to_root')}")
            elif action == 'copy_file':
                target_path_str = step['target_path'].strip('/\\')
                target_path = os.path.join(build_dir, target_path_str)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                shutil.copy(downloaded_file_path, target_path)
                console.print(f"     - {get_text('copy_file', path=step['target_path'])}")
            elif action == 'unzip_folder':
                target_dir = os.path.join(build_dir, step['target_path'].strip('/\\'))
                os.makedirs(target_dir, exist_ok=True)
                with zipfile.ZipFile(downloaded_file_path, 'r') as zf:
                    zf.extractall(target_dir)
                console.print(f"     - {get_text('unzip_folder', path=step['target_path'])}")
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
                            console.print(f"     - {get_text('find_and_copy', filename=member_filename, path=step['target_path'])}")
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
                            console.print(f"     - {get_text('find_and_rename', old_name=os.path.basename(member.filename), new_name=target_filename)}")
                            break
            elif action == 'delete_file':
                path_pattern = os.path.join(build_dir, step['path'].strip('/\\'))
                files_to_delete = glob.glob(path_pattern)
                for f in files_to_delete:
                    if os.path.isfile(f):
                        os.remove(f)
                        console.print(f"     - {get_text('delete_file', filename=os.path.basename(f))}")
        except Exception as e:
            console.print(f"     - [bold red]ERROR[/] {get_text('processing_error', action=action, error=e)}")

def create_final_zip(build_dir, output_filename):
    console.print(f"\n[bold]{get_text('creating_zip')}[/]")
    shutil.make_archive(output_filename.replace('.zip', ''), 'zip', build_dir)
    console.print(f"[bold green]{get_text('zip_created', filename=output_filename)}[/]")

def create_pack_summary(user_choices, categories, output_filename, script_version, content_hash, changes):
    base_path = get_base_path()
    build_dir = os.path.join(base_path, BUILD_DIR)
    summary_filename = os.path.basename(output_filename).replace('.zip', '.txt')
    summary_path = os.path.join(build_dir, summary_filename)
    wib_time = datetime.now(timezone.utc) + timedelta(hours=7)

    content = []
    content.append("===================================")
    content.append(f"HATS Pack Summary (Builder v{script_version})")
    content.append("===================================")
    content.append(f"\nGenerated on: {wib_time.strftime('%Y-%m-%d %H:%M:%S WIB')}")
    content.append(f"Builder Version: {script_version}")
    content.append(f"Content Hash: {content_hash}\n")

    if changes:
        content.append("--- CHANGELOG (What's New Since Last Build) ---")
        for change in changes:
            content.append(change)
        content.append("\n-------------------------------------------------\n")

    content.append("--- INCLUDED COMPONENTS ---")
    for category in categories:
        selections_in_category = {k: v for k, v in user_choices.items() if v['category'] == category}
        if selections_in_category:
            content.append(f"\n--- {category.upper()} ---")
            for comp in selections_in_category.values():
                version = comp.get('asset_info', {}).get('version', 'N/A')
                content.append(f" - {comp['name']} ({version})")
            content.append("")

    try:
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(content))
        console.print(f"[bold green]{get_text('summary_created', filename=summary_filename)}[/]")
    except IOError as e:
        console.print(f"[bold red]ERROR:[/] {get_text('summary_error', error=e)}")

# --- JSON Editor Functions (omitted for brevity, no changes were made) ---
# ... (all functions from load_components to edit_components_menu are unchanged)
def load_components():
    components_path = os.path.join(get_base_path(), COMPONENTS_FILE)
    if not os.path.exists(components_path):
        console.print(f"[yellow]{get_text('error_components_not_found', COMPONENTS_FILE=COMPONENTS_FILE)}[/]")
        return {}
    try:
        with open(components_path, 'r', encoding='utf-8') as f:
            components = json.load(f)
        for comp_id, comp in components.items():
            if 'description' in comp and 'descriptions' not in comp:
                comp['descriptions'] = {'en': comp['description']}
                del comp['description']
        return components
    except (json.JSONDecodeError, IOError) as e:
        console.print(f"[bold red]Error:[/] {get_text('components_load_error', error=e)}")
        return None

def save_components(components):
    components_path = os.path.join(get_base_path(), COMPONENTS_FILE)
    backup_file = components_path + '.bak'
    try:
        if os.path.exists(components_path) and not os.path.exists(backup_file):
            shutil.copy(components_path, backup_file)
            console.print(f"[dim]{get_text('backup_created', backup_file=backup_file)}[/]")
        with open(components_path, 'w', encoding='utf-8') as f:
            json.dump(components, f, indent=2, sort_keys=True)
        with open(components_path, 'r', encoding='utf-8') as f:
            saved_data = json.load(f)
        if saved_data == components:
            console.print(f"[green]{get_text('components_saved', filename=COMPONENTS_FILE)}[/]")
            return True
        else:
            console.print(f"[bold red]Error:[/] {get_text('components_save_verify_error')}")
            return False
    except IOError as e:
        console.print(f"[bold red]Error:[/] {get_text('components_save_error', error=e)}")
        return False
# --- (Rest of JSON editor functions are unchanged) ---

# --- View Component Details ---
def view_component_details(all_components, selected_ids):
    os.system('cls' if os.name == 'nt' else 'clear')
    console.print(Panel(f"[bold white]{get_text('component_details_title')}[/]", style="bold cyan", subtitle=get_text('component_details_subtitle')))
    table = Table(title=f"[bold blue]{get_text('components_title')}[/]")
    table.add_column(get_text('table_name'), style="magenta")
    table.add_column(get_text('table_version'), style="cyan")
    table.add_column(get_text('table_category'), style="green")
    table.add_column(get_text('table_description'), style="white")
    for id, comp in sorted(all_components.items()):
        version = comp.get('asset_info', {}).get('version', 'N/A')
        description = get_component_description(comp, config.get('language', 'en'))
        marker = "[bold green]✓[/]" if id in selected_ids else ""
        table.add_row(f"{comp['name']} {marker}", version, comp['category'], description)
    console.print(table)
    questionary.press_any_key_to_continue(get_text('press_any_key'), style=custom_style).ask()

def run_builder():
    global github_pat
    base_path = get_base_path()
    temp_download_path = os.path.join(base_path, DOWNLOAD_DIR)
    temp_build_path = os.path.join(base_path, BUILD_DIR)

    while True:
        os.system('cls' if os.name == 'nt' else 'clear')
        console.print(Panel(f"[bold white]{get_text('builder_title', VERSION=VERSION)}[/]",
                              style="bold blue", subtitle=get_text('builder_subtitle'),
                              subtitle_align="right"))

        if args.clear_cache and os.path.exists(os.path.join(base_path, CACHE_FILE)):
            os.remove(os.path.join(base_path, CACHE_FILE))
            console.print(f"[yellow]{get_text('cache_cleared', CACHE_FILE=CACHE_FILE)}[/]")

        if github_pat is None:
            console.print(f"[dim]{get_text('pat_info')}[/]")
            pat_input = questionary.password(
                get_text('pat_prompt'), qmark="🔑", style=custom_style
            ).ask()
            if pat_input == 'back':
                return
            github_pat = pat_input if pat_input else None
            if github_pat:
                console.print(f"[green]{get_text('pat_set_success')}[/]")
            else:
                console.print(f"[yellow]{get_text('pat_skipped')}[/]")

        all_components = load_components()
        if all_components is None:
            return

        cache = load_cache()
        with console.status(f"[bold green]{get_text('fetching_info')}[/]") as status:
            total_components = len(all_components)
            for i, (component_id, component) in enumerate(all_components.items()):
                percent_done = int(((i + 1) / total_components) * 100)
                status.update(f"[bold green]{get_text('fetching_progress', percent=percent_done, name=component['name'])}[/]")
                if component.get('source_type') == 'github_release':
                    asset_info = get_release_asset_info(component, github_pat, cache)
                    if asset_info:
                        all_components[component_id]['asset_info'] = asset_info
        save_cache(cache)
        console.print(f"✅ [bold green]{get_text('info_updated')}[/]")

        categories = sorted(list(set(all_components[c]['category'] for c in all_components)))
        choices = []
        for category in categories:
            choices.append(questionary.Separator(f"--- {category.upper()} ---"))
            components_in_category = {k: v for k, v in all_components.items() if v['category'] == category}
            for id, comp in sorted(components_in_category.items()):
                version = comp.get('asset_info', {}).get('version', 'N/A')
                description = get_component_description(comp, config.get('language', 'en'))
                short_description = (description[:37] + '...') if len(description) > 40 else description
                title = f"{comp['name']} ({version}) - {short_description}"
                choices.append(questionary.Choice(title=title, value=id, checked=comp.get('default', False)))
            choices.append(questionary.Separator(" "))
        choices.append(questionary.Separator(f"--- {get_text('options')} ---"))
        choices.append(questionary.Choice(title=get_text('view_details'), value="view_details"))
        choices.append(questionary.Choice(title=get_text('return_to_main'), value="return_to_main"))

        while True:
            selected_ids = questionary.checkbox(
                get_text('select_components'),
                choices=choices,
                style=custom_style,
                instruction=get_text('select_instruction'),
                validate=lambda selections: (
                    True if selections or "return_to_main" in selections or "view_details" in selections
                    else get_text('select_error')
                )
            ).ask()

            if selected_ids is None or "return_to_main" in selected_ids:
                console.print(f"[yellow]{get_text('return_to_main')}[/]")
                return
            if "view_details" in selected_ids:
                selected_ids.remove("view_details")
                view_component_details(all_components, selected_ids)
                continue
            break

        user_choices = {id: all_components[id] for id in selected_ids if id not in ["view_details", "return_to_main"]}

        content_hash = compute_content_hash(user_choices)
        timestamp = datetime.now().strftime('%d%m%Y')
        output_filename = f"{OUTPUT_FILENAME_BASE}-{timestamp}-{content_hash}.zip"
        output_path = os.path.join(base_path, output_filename)

        # --- NEW: Changelog Comparison Logic ---
        last_build = load_last_build()
        last_components = last_build.get('components', {})
        changes = []
        for comp_id, comp_data in sorted(user_choices.items()):
            current_version = comp_data.get('asset_info', {}).get('version', 'N/A')
            last_version = last_components.get(comp_id)
            if last_version is None:
                changes.append(f"* {comp_data['name']}: Newly Added ({current_version})")
            elif last_version != current_version:
                changes.append(f"* {comp_data['name']}: Updated from {last_version} to {current_version}")
        
        # Check for identical build only if no changes were detected
        if not changes and last_build.get('content_hash') == content_hash:
             last_filename = last_build.get('filename')
             if last_filename and os.path.exists(os.path.join(base_path, last_filename)):
                console.print(f"[yellow]{get_text('no_updates', filename=last_filename)}[/]")
                choice = questionary.select(
                    get_text('no_updates_prompt'),
                    choices=[
                        get_text('skip_and_return'),
                        get_text('rebuild_anyway'),
                        get_text('return_to_builder')
                    ], style=custom_style, instruction=get_text('menu_instruction')
                ).ask()
                if choice == get_text('skip_and_return'):
                    console.print(f"[yellow]{get_text('build_skipped')}[/]")
                    return
                elif choice == get_text('return_to_builder'):
                    console.print(f"[yellow]{get_text('return_to_builder')}[/]")
                    continue

        os.system('cls' if os.name == 'nt' else 'clear')
        summary_table = Table(title=f"[bold green]{get_text('pack_summary')}[/]")
        summary_table.add_column(get_text('table_category'), style="blue")
        summary_table.add_column(get_text('table_component'), style="magenta")
        summary_table.add_column(get_text('table_version'), style="cyan")

        for category in categories:
            selections_in_category = [id for id in selected_ids if all_components[id]['category'] == category]
            if selections_in_category:
                for i, id in enumerate(selections_in_category):
                    comp = all_components[id]
                    version = comp.get('asset_info', {}).get('version', 'N/A')
                    category_name = category if i == 0 else ""
                    summary_table.add_row(category_name, comp['name'], version)

        console.print(summary_table)
        console.print(f"[dim]{get_text('proposed_output', filename=output_filename)}[/]")
        confirm_choice = questionary.select(
            get_text('confirm_prompt'),
            choices=[get_text('proceed'), get_text('return_to_builder')],
            style=custom_style, instruction=get_text('menu_instruction')
        ).ask()
        if confirm_choice == get_text('return_to_builder'):
            console.print(f"[yellow]{get_text('return_to_builder')}[/]")
            continue
        elif confirm_choice != get_text('proceed'):
            console.print(f"[yellow]{get_text('return_to_main')}[/]")
            return

        if os.path.exists(temp_download_path): shutil.rmtree(temp_download_path)
        if os.path.exists(temp_build_path): shutil.rmtree(temp_build_path)
        os.makedirs(temp_download_path)
        os.makedirs(temp_build_path)

        try:
            console.print(f"\n[bold]{get_text('starting_build')}[/]")
            skeleton_path = os.path.join(base_path, SKELETON_FILE)
            console.print(f"-> [cyan]{get_text('processing_skeleton', filename=SKELETON_FILE)}[/]")
            with zipfile.ZipFile(skeleton_path, 'r') as zf:
                zf.extractall(temp_build_path)
            console.print(f"  > [green]{get_text('skeleton_extracted')}[/]")
        except FileNotFoundError:
            console.print(f"[bold red]ERROR:[/] {get_text('skeleton_not_found', filename=SKELETON_FILE)}")
            return

        i = 0
        for component_id, component in user_choices.items():
            i += 1
            console.print(f"\n-> [bold][{i}/{len(user_choices)}][/] [bold]{get_text('processing_component', name=component['name'])}[/]")
            asset_info = component.get("asset_info")
            if asset_info and asset_info.get("url"):
                download_url = asset_info["url"]
                console.print(f"  > [dim]{get_text('version', version=asset_info['version'])}[/]")
                filename = component_id + '_' + os.path.basename(download_url).split('?')[0]
                download_path = os.path.join(temp_download_path, filename)
                console.print(f"  > [dim]{get_text('downloading_from', url=download_url.split('?')[0])}[/]")
                if download_file(download_url, download_path, github_pat):
                    process_component(component, download_path, temp_build_path)
            else:
                console.print(f"  > [yellow]{get_text('skip_component')}[/]")

        create_pack_summary(user_choices, categories, output_filename, VERSION, content_hash, changes)
        create_final_zip(temp_build_path, output_path)
        
        # --- NEW: Save detailed build info for next changelog ---
        new_build_info = {
            'content_hash': content_hash,
            'filename': output_filename,
            'timestamp': timestamp,
            'components': {comp_id: data.get('asset_info', {}).get('version', 'N/A') for comp_id, data in user_choices.items()}
        }
        save_last_build(new_build_info)

        if os.path.exists(temp_download_path): shutil.rmtree(temp_download_path)
        if os.path.exists(temp_build_path): shutil.rmtree(temp_build_path)
        console.print(Panel(f"[bold green]{get_text('build_complete')}[/]", subtitle=f"{get_text('output_location', path=output_path)}"))
        questionary.press_any_key_to_continue(get_text('press_any_key'), style=custom_style).ask()
        return

def reset_pat():
    global github_pat
    if github_pat is None:
        console.print(f"[yellow]{get_text('no_pat_set')}[/]")
    elif questionary.confirm(get_text('confirm_clear_pat'), style=custom_style).ask():
        github_pat = None
        console.print(f"[yellow]{get_text('pat_cleared')}[/]")
    else:
        console.print(f"[yellow]{get_text('pat_not_cleared')}[/]")
    questionary.press_any_key_to_continue(get_text('press_any_key'), style=custom_style).ask()

# --- Main Menu ---
def main():
    global config
    config = load_config()
    lang_code = config.get('language', 'en')
    if lang_code not in get_available_languages():
        lang_code = 'en'
        config['language'] = lang_code
        save_config(config)
    load_language(lang_code)
    while True:
        os.system('cls' if os.name == 'nt' else 'clear')
        console.print(Panel(f"[bold white]{get_text('welcome_title', VERSION=VERSION)}[/]",
                              style="bold blue", subtitle=get_text('welcome_subtitle'),
                              subtitle_align="right"))
        choices = [
            get_text('main_menu_builder'),
            get_text('main_menu_editor'),
            get_text('main_menu_clear_cache'),
            get_text('main_menu_change_language'),
            questionary.Separator()
        ]
        if github_pat is not None:
            choices.append(get_text('main_menu_clear_pat'))
        choices.append(get_text('main_menu_exit'))
        choice = questionary.select(
            get_text('main_menu_prompt'),
            choices=choices,
            style=custom_style,
            instruction=get_text('menu_instruction')
        ).ask()
        if choice == get_text('main_menu_builder'):
            run_builder()
        elif choice == get_text('main_menu_editor'):
            # edit_components_menu()  # This function was omitted for brevity
            console.print("[yellow]JSON editor functionality is currently omitted from this script view.[/]")
            questionary.press_any_key_to_continue().ask()
        elif choice == get_text('main_menu_clear_cache'):
            clear_cache()
        elif choice == get_text('main_menu_change_language'):
            change_language()
        elif choice == get_text('main_menu_clear_pat'):
            reset_pat()
        elif choice == get_text('main_menu_exit') or choice is None:
            break

if __name__ == '__main__':
    main()