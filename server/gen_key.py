#!/usr/bin/env python3
"""
Generate a license key and add it to keys.json.

Usage:
  python3 gen_key.py maccleaner owner@email.com        # 3 devices, no expiry
  python3 gen_key.py downloader buyer@email.com 5       # 5 devices
  python3 gen_key.py maccleaner buyer@email.com 3 2027-01-01   # with expiry

Then upload the updated keys.json to the server folder.
"""
import sys, json, secrets, string, os

def make_key(app):
    pre = "ZHMC" if app == "maccleaner" else "ZHDL"
    chunk = lambda: "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
    return f"{pre}-{chunk()}-{chunk()}-{chunk()}"

def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    app    = sys.argv[1]
    owner  = sys.argv[2]
    maxdev = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    expires = sys.argv[4] if len(sys.argv) > 4 else ""

    path = os.path.join(os.path.dirname(__file__), "keys.json")
    keys = {}
    if os.path.exists(path):
        keys = json.load(open(path))

    key = make_key(app)
    while key in keys:
        key = make_key(app)

    keys[key] = {"app": app, "plan": "pro", "active": True,
                 "max_devices": maxdev, "devices": [], "expires": expires, "owner": owner}
    json.dump(keys, open(path, "w"), indent=2)
    print("New license key:\n  " + key)
    print(f"  app={app} owner={owner} devices={maxdev} expires={expires or 'never'}")
    print("Upload keys.json to the server, then send the key to the buyer.")

if __name__ == "__main__":
    main()
