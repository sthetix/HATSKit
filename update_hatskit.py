import shutil
import os
import subprocess
import sys

# Define paths and version
versions_dir = "versions"
latest_file = max([f for f in os.listdir(versions_dir) if f.startswith("hatskit") and f.endswith(".py")],
                 key=lambda x: [int(s) for s in x.split('-')[-1].split('.') if s.isdigit()])
latest_path = os.path.join(versions_dir, latest_file)
target_path = "hatskit.py"

# Extract version from file
with open(latest_path, "r") as source:
    content = source.read()
    version_match = next((m for m in ["version = '1.0.1'", "version = \"1.0.1\""] if m in content), None)
    if version_match:
        version = version_match.split("'")[1] if "'" in version_match else version_match.split('"')[1]
    else:
        version = latest_file.split('-')[-1].replace('.py', '')
tag = f"v{version}"

# Update hatskit.py
with open(latest_path, "r") as source, open(target_path, "w") as target:
    target.write(content)
print(f"Updated {target_path} with {latest_file}")

# Build .exe with PyInstaller (using string command)
pyinstaller_cmd = f'pyinstaller --onefile --name "HATSkit-{version}" --add-data "{os.path.join(os.getcwd(), "components.json")};." --add-data "{os.path.join(os.getcwd(), "skeleton.zip")};." "{os.path.join(os.getcwd(), "hatskit.py")}"'
subprocess.run(pyinstaller_cmd, shell=True, check=True)
exe_source = os.path.join("dist", f"HATSkit-{version}.exe")
exe_target = "HATSkit.exe"

# Move .exe to main directory
if os.path.exists(exe_source):
    shutil.move(exe_source, exe_target)
    print(f"Moved {exe_source} to {exe_target}")
else:
    print(f"Error: {exe_source} not found!")
    sys.exit(1)

# Clean up
shutil.rmtree("build", ignore_errors=True)
shutil.rmtree("dist", ignore_errors=True)
shutil.rmtree("__pycache__", ignore_errors=True)

# Stage, commit, and tag
subprocess.run(["git", "add", target_path, exe_target], check=True)
subprocess.run(["git", "commit", "-m", f"Update to {version} with HATSkit.exe"], check=True)
subprocess.run(["git", "tag", tag], check=True)

# Prompt for push confirmation
response = input(f"Proceed to push to GitHub with tag {tag}? (yes/no): ").lower()
if response == "yes":
    subprocess.run(["git", "push", "origin", "main"], check=True)
    subprocess.run(["git", "push", "origin", tag], check=True)
    print("Push completed successfully!")
else:
    print("Push cancelled. Commit and tag are staged locally.")