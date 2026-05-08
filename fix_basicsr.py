"""
Patches basicsr 1.4.2 setup.py and installs it locally.
exec(compile(...)) without a namespace dict doesn't populate locals() in
Python 3.  Fix: pass an explicit dict as the namespace.
"""
import subprocess, sys, os, tarfile, re, tempfile, shutil, urllib.request

tmp = tempfile.mkdtemp(prefix="basicsr_fix_")
print(f"Working directory: {tmp}")

try:
    url = "https://files.pythonhosted.org/packages/source/b/basicsr/basicsr-1.4.2.tar.gz"
    dest = os.path.join(tmp, "basicsr-1.4.2.tar.gz")
    print("Downloading basicsr 1.4.2 source from PyPI...")
    urllib.request.urlretrieve(url, dest)
    print("Download complete.")

    src_dir = os.path.join(tmp, "basicsr-1.4.2")
    print("Extracting...")
    with tarfile.open(dest) as t:
        t.extractall(tmp, filter="data")

    setup_py = os.path.join(src_dir, "setup.py")
    code = open(setup_py, encoding="utf-8").read()

    OLD = (
        "def get_version():\n"
        "    with open(version_file, 'r') as f:\n"
        "        exec(compile(f.read(), version_file, 'exec'))\n"
        "    return locals()['__version__']"
    )

    NEW = (
        "def get_version():\n"
        "    _ns = {}\n"
        "    with open(version_file, 'r') as f:\n"
        "        exec(compile(f.read(), version_file, 'exec'), _ns)\n"
        "    return _ns['__version__']"
    )

    if OLD not in code:
        print("ERROR: Expected function body not found. Current get_version():")
        m = re.search(r"def get_version\(\):.*?(?=\n\S)", code, re.DOTALL)
        print(m.group() if m else "(not found)")
        sys.exit(1)

    patched = code.replace(OLD, NEW)
    open(setup_py, "w", encoding="utf-8").write(patched)
    print("Patched setup.py successfully.\n")

    print("Installing patched basicsr...")
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", src_dir, "--no-build-isolation",
    ])
    print("\nbasicsr installed successfully!")

finally:
    shutil.rmtree(tmp, ignore_errors=True)
