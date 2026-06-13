# ZH Motions — License Server (self-hosted)

Free app, Pro features unlocked by a license key. The app POSTs the key to your
server, which checks `keys.json` and binds the device.

## Endpoint
The app calls: `https://www.zhmotions.com/api/license/verify.php`
(set in `LICENSE_URL` inside the app code — change there if your path differs.)

## Deploy (PHP host / cPanel)
1. On zhmotions.com hosting, create folder: `public_html/api/license/`
2. Upload **verify.php** and **keys.json** there.
3. Test in a browser/terminal:
   ```bash
   curl -X POST https://www.zhmotions.com/api/license/verify.php \
        -d "key=ZHMC-DEMO-1234-5678&app=maccleaner&device=testdevice"
   # → {"valid":true,"plan":"pro","message":"OK"}
   ```
4. (Security) Make `keys.json` non-public if your host allows: chmod 600, or move
   it outside web root and update the path in verify.php (`$store`).

## Make & sell keys
```bash
python3 gen_key.py maccleaner buyer@email.com 3      # generates a key, adds to keys.json
```
Upload the updated `keys.json`, email the key to the buyer. They paste it into
the app's **⭐ Pro** tab → Activate.

## Manage
- Deactivate a key: set its `"active": false` in keys.json, re-upload.
- Reset devices: empty its `"devices": []`.
- Expiry: set `"expires": "2027-01-01"`.

## Not on PHP hosting? (e.g. Framer site)
Framer/static hosts can't run PHP. Options:
- Put the API on a **subdomain** with PHP (e.g. `api.zhmotions.com` on cheap shared hosting).
- Or use a **Cloudflare Worker** / **Vercel function** with the same logic + a KV store,
  and point `LICENSE_URL` to it.

## Payments
Pair with Gumroad/Lemon Squeezy/Stripe: on purchase, run `gen_key.py` (or a webhook)
to create the key and email it automatically.
