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
                path_pattern = os.path.join(build_dir, step.get('target_path', step.get('path', '')).strip('/\\'))
                items_to_delete = glob.glob(path_pattern)
                for item in items_to_delete:
                    if os.path.isfile(item):
                        os.remove(item)
                        console.print(f"     - {get_text('delete_file', filename=os.path.basename(item))}")
                    elif os.path.isdir(item):
                        shutil.rmtree(item)
                        console.print(f"     - Deleted folder: {os.path.basename(item)}")
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

# --- JSON Editor Functions ---
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

def view_components(components):
    if not components:
        console.print(f"[yellow]{get_text('no_components')}[/]")
        return
    table = Table(title=f"[bold blue]{get_text('components_title')}[/]")
    table.add_column(get_text('table_id'), style="cyan", no_wrap=True)
    table.add_column(get_text('table_name'), style="magenta")
    table.add_column(get_text('table_category'), style="green")
    table.add_column(get_text('table_repo_url'), style="yellow")
    table.add_column(get_text('table_description'), style="white")
    for comp_id, details in sorted(components.items()):
        repo_or_url = details.get('repo', details.get('url', 'N/A'))
        description = get_component_description(details, config.get('language', 'en'))
        table.add_row(comp_id, details.get('name', 'N/A'), details.get('category', 'N/A'), repo_or_url, description)
    console.print(table)

def get_processing_step():
    choices = [
        "unzip_to_root",
        "copy_file",
        "unzip_folder",
        "find_and_copy",
        "find_and_rename",
        questionary.Choice(title="Delete File or Folder", value="delete_file")
    ]
    action = questionary.select(
        get_text('action_prompt'),
        choices=choices,
        style=custom_style
    ).ask()

    if not action: return None
    step = {"action": action}

    actions_needing_path = ["copy_file", "unzip_folder", "find_and_copy", "find_and_rename", "delete_file"]
    if action in actions_needing_path:
        prompt_text = get_text('target_path_prompt_delete') if action == 'delete_file' else get_text('target_path_prompt')
        step['target_path'] = questionary.text(prompt_text, style=custom_style).ask()

    if action in ["find_and_copy", "find_and_rename"]:
        step['source_file_pattern'] = questionary.text(get_text('source_pattern_prompt'), style=custom_style).ask()
    if action == "find_and_rename":
        step['target_filename'] = questionary.text(get_text('target_filename_prompt'), style=custom_style).ask()

    return step

def edit_processing_step(step):
    edited_step = step.copy()
    choices = [
        "unzip_to_root",
        "copy_file",
        "unzip_folder",
        "find_and_copy",
        "find_and_rename",
        questionary.Choice(title="Delete File or Folder", value="delete_file")
    ]
    edited_step['action'] = questionary.select(
        get_text('action_prompt'),
        choices=choices,
        default=edited_step.get('action', 'copy_file'),
        style=custom_style
    ).ask()

    if not edited_step['action']: return step

    actions_needing_path = ["copy_file", "unzip_folder", "find_and_copy", "find_and_rename", "delete_file"]
    if edited_step['action'] in actions_needing_path:
        prompt_text = get_text('target_path_prompt_delete') if edited_step['action'] == 'delete_file' else get_text('target_path_prompt')
        edited_step['target_path'] = questionary.text(
            prompt_text,
            default=edited_step.get('target_path', ''),
            style=custom_style
        ).ask()

    if edited_step['action'] in ["find_and_copy", "find_and_rename"]:
        edited_step['source_file_pattern'] = questionary.text(
            get_text('source_pattern_prompt'),
            default=edited_step.get('source_file_pattern', ''),
            style=custom_style
        ).ask()
    if edited_step['action'] == "find_and_rename":
        edited_step['target_filename'] = questionary.text(
            get_text('target_filename_prompt'),
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

def format_value_for_display(value, lang_code=None):
    if isinstance(value, dict) and lang_code:
        return get_component_description({'descriptions': value}, lang_code)
    elif isinstance(value, list):
        if not value:
            return "[]"
        return "; ".join([f"{step.get('action', 'N/A')}: {', '.join([f'{k}={v}' for k, v in step.items() if k != 'action'])}" for step in value])
    elif value is None:
        return ""
    return str(value)

def add_component(components):
    console.print(Panel(f"[bold white]{get_text('add_component_title')}[/]", style="bold green"))
    new_id = questionary.text(get_text('new_id_prompt'), style=custom_style).ask()
    if not new_id or new_id in components:
        console.print(f"[bold red]Error:[/] {get_text('id_error')}")
        return components
    new_comp = {}
    new_comp['name'] = questionary.text(get_text('name_prompt'), style=custom_style).ask()
    current_lang = config.get('language', 'en')
    new_comp['descriptions'] = {current_lang: questionary.text(get_text('description_prompt'), style=custom_style).ask() or 'No description'}
    new_comp['category'] = questionary.select(get_text('category_prompt'), choices=["Essential", "Homebrew Apps", "Patches", "Tesla Overlays", "Payloads"], style=custom_style).ask()
    new_comp['default'] = questionary.confirm(get_text('default_prompt'), style=custom_style).ask()
    new_comp['source_type'] = questionary.select(get_text('source_type_prompt'), choices=["github_release", "direct_url"], style=custom_style).ask()

    if new_comp['source_type'] == 'github_release':
        new_comp['repo'] = questionary.text(get_text('repo_prompt'), style=custom_style).ask()
        if questionary.confirm(get_text('specific_tag_prompt'), style=custom_style).ask():
            new_comp['tag'] = questionary.text(get_text('tag_prompt'), style=custom_style).ask()
    else:
        new_comp['url'] = questionary.text(get_text('url_prompt'), style=custom_style).ask()

    new_comp['asset_pattern'] = questionary.text(get_text('asset_pattern_prompt'), style=custom_style).ask()
    steps = []
    while questionary.confirm(get_text('add_step_prompt'), style=custom_style).ask():
        steps.append(get_processing_step())
    new_comp['processing_steps'] = steps
    summary_table = Table(title=f"[bold yellow]{get_text('review_component', id=new_id)}[/]")
    summary_table.add_column(get_text('table_field'), style="cyan")
    summary_table.add_column(get_text('table_value'), style="white")
    for key, value in new_comp.items():
        if key == 'descriptions':
            value = get_component_description(new_comp, current_lang)
        summary_table.add_row(key, str(value))
    console.print(summary_table)
    if questionary.confirm(get_text('save_component_prompt'), style=custom_style).ask():
        components[new_id] = new_comp
        console.print(f"\n[green]{get_text('component_added', name=new_comp['name'])}[/]")
        if save_components(components):
            components = load_components()
    else:
        console.print(f"\n[yellow]{get_text('add_cancelled')}[/]")
    return components

def edit_component(components):
    if not components:
        console.print(f"[yellow]{get_text('no_components')}[/]")
        return components
    choices = [questionary.Choice(f"{details['name']} ({comp_id})", value=comp_id) for comp_id, details in sorted(components.items())]
    comp_id_to_edit = questionary.select(get_text('edit_component_prompt'), choices=choices, style=custom_style, instruction=get_text('menu_instruction')).ask()
    if not comp_id_to_edit:
        return components
    original_comp = copy.deepcopy(components[comp_id_to_edit])
    comp = components[comp_id_to_edit].copy()
    console.print(f"[bold]--- {get_text('editing_component', name=comp['name'])} ---[/]")
    console.print(f"[dim]{get_text('press_enter')}[/dim]")
    current_lang = config.get('language', 'en')

    comp['name'] = questionary.text(get_text('name_prompt'), default=str(comp['name']), style=custom_style).ask()
    comp['descriptions'][current_lang] = questionary.text(get_text('description_prompt'), default=get_component_description(comp, current_lang), style=custom_style).ask() or get_component_description(comp, current_lang)
    comp['category'] = questionary.text(get_text('category_prompt'), default=str(comp['category']), style=custom_style).ask()
    comp['default'] = questionary.confirm(get_text('default_prompt'), default=comp.get('default', False), style=custom_style).ask()
    comp['source_type'] = questionary.select(get_text('source_type_prompt'), choices=["github_release", "direct_url"], default=comp.get('source_type', 'github_release'), style=custom_style).ask()

    if comp['source_type'] == 'github_release':
        comp.pop('url', None)
        comp['repo'] = questionary.text(get_text('repo_prompt'), default=str(comp.get('repo', '')), style=custom_style).ask()
        if questionary.confirm(get_text('specific_tag_prompt'), default=bool(comp.get('tag')), style=custom_style).ask():
            comp['tag'] = questionary.text(get_text('tag_prompt'), default=str(comp.get('tag', '')), style=custom_style).ask()
        else:
            comp.pop('tag', None)
    else:
        comp.pop('repo', None)
        comp.pop('tag', None)
        comp['url'] = questionary.text(get_text('url_prompt'), default=str(comp.get('url', '')), style=custom_style).ask()

    comp['asset_pattern'] = questionary.text(get_text('asset_pattern_prompt'), default=str(comp['asset_pattern']), style=custom_style).ask()

    if questionary.confirm(get_text('edit_steps_prompt'), style=custom_style).ask():
        steps = comp.get('processing_steps', [])
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            console.print(Panel(f"[bold white]{get_text('steps_editor_title')}[/]", style="bold cyan", subtitle=f"{get_text('for_component', name=comp['name'])}"))
            if not steps:
                console.print(f"[yellow]{get_text('no_steps')}[/]")
            else:
                for i, step in enumerate(steps):
                    console.print(f"  [bold cyan]{i+1}:[/] {step}")
            action = questionary.select(
                get_text('steps_action_prompt'),
                choices=[
                    get_text('add_step'),
                    get_text('edit_step'),
                    get_text('delete_step'),
                    get_text('finish_steps')
                ],
                style=custom_style
            ).ask()
            if action == get_text('add_step'):
                new_step = get_processing_step()
                if new_step: steps.append(new_step)
            elif action == get_text('edit_step'):
                if not steps: continue
                step_index_str = questionary.text(get_text('step_number_prompt', count=len(steps)), style=custom_style).ask()
                try:
                    step_index = int(step_index_str) - 1
                    if 0 <= step_index < len(steps):
                        console.print(f"Editing step {step_index+1}: {steps[step_index]}")
                        edited = edit_processing_step(steps[step_index])
                        if edited: steps[step_index] = edited
                    else:
                        console.print(f"[red]{get_text('invalid_number')}[/]")
                except (ValueError, TypeError):
                    console.print(f"[red]{get_text('invalid_input')}[/]")
            elif action == get_text('delete_step'):
                if not steps: continue
                step_index_str = questionary.text(get_text('step_number_prompt', count=len(steps)), style=custom_style).ask()
                try:
                    step_index = int(step_index_str) - 1
                    if 0 <= step_index < len(steps):
                        deleted = steps.pop(step_index)
                        console.print(f"{get_text('step_deleted', step=str(deleted))}")
                    else:
                        console.print(f"[red]{get_text('invalid_number')}[/]")
                except (ValueError, TypeError):
                    console.print(f"[red]{get_text('invalid_input')}[/]")
            elif action == get_text('finish_steps') or action is None:
                break
        comp['processing_steps'] = steps

    summary_table = Table(title=f"[bold yellow]{get_text('review_changes', id=comp_id_to_edit)}[/]")
    summary_table.add_column(get_text('table_field'), style="cyan")
    summary_table.add_column(get_text('table_old_value'), style="red")
    summary_table.add_column(get_text('table_new_value'), style="green")
    all_keys = set(original_comp.keys()) | set(comp.keys())
    for key in sorted(list(all_keys)):
        old_val = format_value_for_display(original_comp.get(key), current_lang if key == 'descriptions' else None)
        new_val = format_value_for_display(comp.get(key), current_lang if key == 'descriptions' else None)
        if old_val != new_val:
            summary_table.add_row(key, old_val, f"[bold]{new_val}[/]")

    console.print(summary_table)
    if questionary.confirm(get_text('save_changes_prompt'), style=custom_style).ask():
        components[comp_id_to_edit] = comp
        console.print(f"\n[green]{get_text('component_updated', name=comp['name'])}[/]")
        if save_components(components):
            components = load_components()
    else:
        console.print(f"\n[yellow]{get_text('edit_cancelled')}[/]")
    return components

def delete_component(components):
    if not components:
        console.print(f"[yellow]{get_text('no_components')}[/]")
        return components
    choices = [questionary.Choice(f"{details['name']} ({comp_id})", value=comp_id) for comp_id, details in sorted(components.items())]
    comp_id_to_delete = questionary.select(get_text('delete_component_prompt'), choices=choices, style=custom_style).ask()
    if not comp_id_to_delete:
        return components
    comp_name = components[comp_id_to_delete]['name']
    if questionary.confirm(get_text('confirm_delete', name=comp_name), style=custom_style).ask():
        del components[comp_id_to_delete]
        console.print(f"[green]{get_text('component_deleted', name=comp_name)}[/]")
        if save_components(components):
            components = load_components()
    else:
        console.print(f"[yellow]{get_text('delete_cancelled')}[/]")
    return components

def edit_components_menu():
    components = load_components()
    if components is None:
        return
    components_path = os.path.join(get_base_path(), COMPONENTS_FILE)
    backup_file = components_path + '.bak'
    if os.path.exists(components_path) and not os.path.exists(backup_file):
        try:
            shutil.copy(components_path, backup_file)
            console.print(f"[dim]{get_text('backup_created', backup_file=backup_file)}[/]")
        except IOError as e:
            console.print(f"[yellow]WARNING:[/] Could not create backup: {e}")

    while True:
        os.system('cls' if os.name == 'nt' else 'clear')
        console.print(Panel(f"[bold white]{get_text('component_editor_title')}[/]",
                           style="bold magenta", subtitle=get_text('component_editor_subtitle'),
                           subtitle_align="right"))
        choice = questionary.select(
            get_text('editor_action_prompt'),
            choices=[
                get_text('view_components'),
                get_text('add_component'),
                get_text('edit_component'),
                get_text('delete_component'),
                questionary.Separator(),
                get_text('return_to_main')
            ],
            style=custom_style,
            instruction=get_text('menu_instruction')
        ).ask()
        if choice == get_text('view_components'):
            view_components(components)
            questionary.press_any_key_to_continue(get_text('press_any_key'), style=custom_style).ask()
        elif choice == get_text('add_component'):
            components = add_component(components)
            questionary.press_any_key_to_continue(get_text('press_any_key'), style=custom_style).ask()
        elif choice == get_text('edit_component'):
            components = edit_component(components)
            questionary.press_any_key_to_continue(get_text('press_any_key'), style=custom_style).ask()
        elif choice == get_text('delete_component'):
            components = delete_component(components)
            questionary.press_any_key_to_continue(get_text('press_any_key'), style=custom_style).ask()
        elif choice == get_text('return_to_main') or choice is None:
            return

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
    global github_pat, config
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
            console.print(f"[dim]{get_text('pat_info')}[/dim]")
            console.print(f"[dim]{get_text('pat_save_warning')}[/dim]") # FIXED: Removed yellow color to fix tag mismatch
            pat_input = questionary.password(
                get_text('pat_prompt'), qmark="🔑", style=custom_style
            ).ask()
            if pat_input == 'back':
                return
            github_pat = pat_input if pat_input else None
            if github_pat:
                config['github_pat'] = github_pat
                save_config(config)
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

        last_build = load_last_build()
        last_components = last_build.get('components', {})
        changes = []

        for comp_id, comp_data in sorted(user_choices.items()):
            current_version = comp_data.get('asset_info', {}).get('version', 'N/A')
            last_comp_info = last_components.get(comp_id)
            if last_comp_info is None:
                changes.append(f"* {comp_data['name']}: Newly Added ({current_version})")
            elif last_comp_info.get('version') != current_version:
                changes.append(f"* {comp_data['name']}: Updated from {last_comp_info.get('version')} to {current_version}")

        for comp_id, last_comp_info in sorted(last_components.items()):
            if comp_id not in user_choices:
                changes.append(f"* {last_comp_info.get('name', comp_id)}: Removed (was {last_comp_info.get('version')})")

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

        new_build_info = {
            'content_hash': content_hash,
            'filename': output_filename,
            'timestamp': timestamp,
            'components': {
                comp_id: {
                    'name': data['name'],
                    'version': data.get('asset_info', {}).get('version', 'N/A')
                } for comp_id, data in user_choices.items()
            }
        }
        save_last_build(new_build_info)

        if os.path.exists(temp_download_path): shutil.rmtree(temp_download_path)
        if os.path.exists(temp_build_path): shutil.rmtree(temp_build_path)
        console.print(Panel(f"[bold green]{get_text('build_complete')}[/]", subtitle=f"{get_text('output_location', path=output_path)}"))
        questionary.press_any_key_to_continue(get_text('press_any_key'), style=custom_style).ask()
        return

def reset_pat():
    global github_pat, config
    if github_pat is None:
        console.print(f"[yellow]{get_text('no_pat_set')}[/]")
    elif questionary.confirm(get_text('confirm_clear_pat'), style=custom_style).ask():
        github_pat = None
        config.pop('github_pat', None)
        save_config(config)
        console.print(f"[yellow]{get_text('pat_cleared')}[/]")
    else:
        console.print(f"[yellow]{get_text('pat_not_cleared')}[/]")
    questionary.press_any_key_to_continue(get_text('press_any_key'), style=custom_style).ask()

# --- Main Menu ---
def main():
    global config, github_pat
    config = load_config()
    github_pat = config.get('github_pat')
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
            edit_components_menu()
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