"""Bootstrap helper — runs train_all.py from the correct working directory."""
import subprocess, sys, os
script = os.path.join(os.path.dirname(__file__), "train_all.py")
result = subprocess.run([sys.executable, script], cwd=os.path.dirname(__file__))
sys.exit(result.returncode)
