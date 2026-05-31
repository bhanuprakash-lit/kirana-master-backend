"""
One-shot script: reads serviceAccountKey.json and patches the Container App env var
via az rest (avoids all CLI argument-parsing issues with JSON values).
Run from repo root with Azure CLI logged in.
"""
import json, subprocess, sys, tempfile, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def az(*args):
    result = subprocess.run(["az"] + list(args), capture_output=True, text=True, shell=True)
    if result.returncode != 0:
        print("AZ ERROR:", result.stderr)
        sys.exit(1)
    return result.stdout.strip()

# Load Firebase creds as compact single-line JSON
key_path = os.path.join(ROOT, "serviceAccountKey.json")
with open(key_path) as f:
    firebase_json = json.dumps(json.load(f))
print(f"Loaded serviceAccountKey.json ({len(firebase_json)} chars)")

# Get current container app config
print("Fetching current Container App config...")
raw = az("containerapp", "show", "--name", "ca-lohiya-outlet",
         "--resource-group", "rg-lohiya-outlet-dev", "-o", "json")
app = json.loads(raw)

# Patch env vars
env = app["properties"]["template"]["containers"][0]["env"]
env = [e for e in env if e.get("name") != "FIREBASE_CREDENTIALS_JSON"]
env.append({"name": "FIREBASE_CREDENTIALS_JSON", "value": firebase_json})
app["properties"]["template"]["containers"][0]["env"] = env
print(f"Env vars: {[e['name'] for e in env]}")

# Build a clean minimal PATCH body — reconstruct only safe writable fields
# (avoids rejections from read-only/newer-API-version fields)
container = app["properties"]["template"]["containers"][0]
patch_body = {
    "location": app["location"],
    "properties": {
        "template": {
            "containers": [{
                "name":      container["name"],
                "image":     container["image"],
                "resources": container.get("resources", {}),
                "env":       env,
            }],
            "scale": {
                "minReplicas": app["properties"]["template"]["scale"].get("minReplicas"),
                "maxReplicas": app["properties"]["template"]["scale"].get("maxReplicas", 10),
            },
        },
    },
}

# Write patched body to temp file
tf = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
json.dump(patch_body, tf)
tf.close()
print(f"Body written to: {tf.name}")

# PATCH via az rest (reads body from file — no arg-parsing issues)
sub = "870f8134-85c4-4dd8-b598-9882b99bf6e8"
rg  = "rg-lohiya-outlet-dev"
url = (f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}"
       f"/providers/Microsoft.App/containerApps/ca-lohiya-outlet?api-version=2023-05-01")

print("Applying PATCH via az rest...")
out = az("rest", "--method", "patch", "--url", url, f"--body=@{tf.name}")
os.unlink(tf.name)

result = json.loads(out) if out else {}
revision = result.get("properties", {}).get("latestRevisionName", "unknown")
print(f"Done! Latest revision: {revision}")
