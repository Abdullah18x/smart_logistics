# API collection

Everything here is **auto-generated** from the four services' FastAPI routes and
merged into the single API the gateway exposes on `http://localhost:8000`. Do not
edit by hand. Regenerate after any route change:

```bash
./dev.sh docs          # Windows: .\dev.ps1 docs  or  dev docs   ·   make docs
```

No service has to be running: each app is imported and asked for its spec.

| File | What it is |
|---|---|
| `index.html` | **Browsable reference — open this first.** Every endpoint, labelled with its service, with a copy-ready curl command |
| `openapi.json` | The merged OpenAPI 3.1 spec (`x-service` on each operation names the owner) |
| `SmartLogistics.postman_collection.json` | Postman collection: one folder per service, sub-folders per tag |
| `SmartLogistics.postman_environment.json` | Postman environment with `base_url`, tokens and path variables |
| `env.sh` | Shell helper — logs in and exports `ACCESS_TOKEN` |
| `<tag>.sh` | Runnable curl commands per tag: `authentication`, `users`, `warehouses`, `catalog`, `inventory`, `reservations`, `shipments` |
| `health.sh` | Liveness/readiness of each service on its own port (probes are not routed through the gateway) |

## Interactive: Swagger UI

With the stack running (`./dev.sh infra up` + `./dev.sh start`, or `./dev.sh docker up`),
open **http://localhost:8000/docs**.

- Pick a service in the **Select a definition** dropdown, top right.
- Click **Authorize** once with an `access_token`. It is kept when you switch service
  and when you reload.
- **Try it out** goes through the gateway, exactly like a real client.

The `/health` probes listed under each service only answer on that service's own port.
Each service also keeps its own Swagger UI at `http://localhost:800{1,2,3,4}/docs`.

## Browser (offline)

Open `docs/curl/index.html` in any browser — no server needed.

Set **Base URL** and **Access token** at the top once; they are substituted into every command
on the page and remembered in that browser. Path parameters (`shipment_id`, `warehouse_id`, …)
appear as variables too, so a copied command is ready to run.

To get a token: expand **POST /api/v1/auth/login**, copy, run it, paste the `access_token` into
the field.

## Postman

1. **Import** → drop in `SmartLogistics.postman_collection.json` **and**
   `SmartLogistics.postman_environment.json`
2. Select **SmartLogistics — Local** in the environment dropdown, top right
3. Run **identity service → Authentication → Authenticate and receive a token pair**

Login and refresh have a test script that writes `access_token` and `refresh_token` into the
environment, so every other request is authorised without copy-pasting.

Path variables such as `{{shipment_id}}` are set in the environment, or per-request under
**Vars**. Query parameters and the `Idempotency-Key` header are included but disabled — tick
the ones you want.

> Prefer importing `openapi.json` if you would rather Postman generate the collection itself.
> Insomnia, Bruno, Hoppscotch and Swagger UI all accept the same file.

## Shell

```bash
source docs/curl/env.sh                 # logs in through the gateway, exports ACCESS_TOKEN
curl -s "$BASE_URL/api/v1/shipments" -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

The generated `.sh` files hold one ready-made command per endpoint; copy the one you need
or run the whole file.
