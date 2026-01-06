import os
import ast
import sys
import sysconfig
from collections import defaultdict

# --------------------------------------------------------------------
# 1. Collect standard-library module names
# --------------------------------------------------------------------
def get_stdlib_modules():
    """Return a set of standard-library module names for this Python version."""
    stdlib_path = sysconfig.get_paths()["stdlib"]
    modules = set(sys.builtin_module_names)
    for root, _, files in os.walk(stdlib_path):
        for f in files:
            if f.endswith(".py"):
                modules.add(f[:-3])
    return modules


# --------------------------------------------------------------------
# 2. Scan only the current folder (no recursion)
# --------------------------------------------------------------------
def scan_imports(base_path="."):
    """Collect imports from all .py files in base_path (non-recursive)."""
    imports = defaultdict(set)
    for file in os.listdir(base_path):
        if not file.endswith(".py") or file.startswith("test_"):
            continue
        path = os.path.join(base_path, file)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        mod = alias.name.split(".")[0]
                        imports[mod].add(file)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        mod = node.module.split(".")[0]
                        imports[mod].add(file)
        except Exception as e:
            print(f"Skipped {path}: {e}")
    return imports


# --------------------------------------------------------------------
# 3. Map import name → pip package name (Spectro Capture core stack)
# --------------------------------------------------------------------
PACKAGE_ALIASES = {
    # --- astronomy / science ---
    "astropy": "astropy",
    "ephem": "ephem",
    "numpy": "numpy",
    "photutils": "photutils",

    # --- GUI / image / plotting ---
    "tkinter": "tkinter",        # stdlib
    "ttkbootstrap": "ttkbootstrap",
    "PIL": "Pillow",
    "ImageTk": "Pillow",
    "matplotlib": "matplotlib",
    "plotly": "plotly",

    # --- Windows / ASCOM / device control ---
    "win32com": "pywin32",
    "pythoncom": "pywin32",
    "pywintypes": "pywin32",
    "serial": "pyserial",

    # --- data / utilities ---
    "pandas": "pandas",
    "datetime": "datetime",      # stdlib
    "threading": "threading",    # stdlib
    "subprocess": "subprocess",  # stdlib
    "json": "json",              # stdlib
}


def map_to_pip_name(import_name):
    """Return pip package name for import, or leave unchanged if unknown."""
    return PACKAGE_ALIASES.get(import_name, import_name)


# --------------------------------------------------------------------
# 4. Main
# --------------------------------------------------------------------
if __name__ == "__main__":
    print("Scanning current folder for Python dependencies...\n")

    stdlib = get_stdlib_modules()
    all_imports = scan_imports(".")

    # Filter out built-ins and __future__
    filtered = {mod: files for mod, files in all_imports.items() if mod not in stdlib and mod != "__future__"}

    # Map to pip names and group by package
    mapped = defaultdict(set)
    for mod, files in filtered.items():
        pkg = map_to_pip_name(mod)
        mapped[pkg].update(files)

    # Deduplicate and sort
    pip_names = sorted(mapped.keys())
    total_scripts = len({f for files in mapped.values() for f in files})

    # Print summary
    print("External dependencies found:\n")
    for pkg in pip_names:
        files = ", ".join(sorted(mapped[pkg]))
        print(f"  {pkg:<15}  ←  {files}")

    # Prepare pip install line
    pip_command = "pip install " + " ".join(pip_names)

    # Write simple list + pip install line
    with open("requirements_detected.txt", "w", encoding="utf-8") as f:
        for pkg in pip_names:
            f.write(pkg + "\n")
        f.write("\n# To install all dependencies, run:\n")
        f.write("# " + pip_command + "\n")

    # Write detailed mapping
    with open("requirements_detailed.txt", "w", encoding="utf-8") as f:
        for pkg in pip_names:
            f.write(f"{pkg}  ←  {', '.join(sorted(mapped[pkg]))}\n")

    # Final summary
    print(f"\nSummary:")
    print(f"  {len(pip_names)} external dependencies detected")
    print(f"  Found across {total_scripts} Python scripts")
    print(f"\nSaved:")
    print(f"  requirements_detected.txt   (simple list + pip command)")
    print(f"  requirements_detailed.txt   (with file references)")
    print(f"\nTo install all dependencies, run:\n  {pip_command}")
