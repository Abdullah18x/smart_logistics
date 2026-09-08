# API collection

Everything here is **auto-generated** from the live FastAPI routes. Do not edit by hand —
regenerate after any route change:

```bash
make api
```

| File | What it is |
|---|---|
| `index.html` | **Browsable reference — open this first.** Every endpoint with a copy-ready curl command |
| `openapi.json` | The raw OpenAPI 3.1 spec |
| `SmartLogistics.postman_collection.json` | Postman collection, grouped by tag |
| `SmartLogistics.postman_environment.json` | Postman environment with `base_url` and token variables |
| `env.sh` | Shell helper — logs in and exports `ACCESS_TOKEN` |
| `authentication.sh`, `users.sh`, `health.sh` | Runnable curl commands per tag |

## Browser

Open `docs/curl/index.html` in any browser — no server needed.

Set **Base URL** and **Access token** at the top once; they are substituted into every command
on the page and remembered in that browser. Path parameters (`shipment_id`, `warehouse_id`, …)
appear as variables too, so a copied command is ready to run.

To get a token: expand **POST /api/v1/auth/login**, copy, run it, paste the `access_token` into
the field.

Each command has a **Copy** button. In Postman: **Import → Raw text**, paste, done.

## Postman

1. **Import** → drop in `SmartLogistics.postman_collection.json` **and**
   `SmartLogistics.postman_environment.json`
2. Select **SmartLogistics — Local** in the environment dropdown, top right
3. Run **Authentication → Authenticate and receive a token pair**

The login request has a test script that writes `access_token` and `refresh_token` into the
environment automatically, so every other request is authorised without copy-pasting.

Path variables such as `{{user_id}}` are set in the environment, or per-request under **Vars**.
Query parameters are included but disabled — tick the ones you want.

> Prefer importing `openapi.json` if you would rather Postman generate the collection itself.
> Insomnia, Bruno, Hoppscotch and Swagger UI all accept the same file.

## Shell

```bash
source docs/curl/env.sh                 # logs in, exports ACCESS_TOKEN
curl -s "$BASE_URL/api/v1/users" -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

The generated `.sh` files hold one ready-made command per endpoint; copy the one you need
or run the whole file.
