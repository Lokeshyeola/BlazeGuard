# BlazeGuard API Communication Layer

## 1. What is it?
A middle layer of code that sits between the **Frontend** and the **BlazeGuard Backend**. Instead of the frontend calling `fetch()`/`axios` directly all over the codebase, every request goes through this one module.

## 2. Why do we need it in BlazeGuard?
- Without it: every frontend component would duplicate URL-building, headers, error handling → messy and inconsistent.
- With it: one place to change the base URL, auth method, timeout, or error format. Easier to maintain, test, and secure.

## 3. How does it work?
- `api_client.py` — the core engine: builds headers (incl. auth token), sends the request, handles timeouts, normalizes errors into a single `ApiError` class.
- `blazeguard_endpoints.py` — a friendly list of named functions (`get_status()`, `start_scan()`, etc.) that map to real BlazeGuard routes. This is what the frontend/consumer code actually imports.

## 4. Simple flow
```
Frontend / Consumer code
      |
      v
blazeguard_api.get_status()    <- endpoint function
      |
      v
api_client.get("/status")      <- core client (adds headers, base URL)
      |
      v
requests library -> BlazeGuard Backend
      |
      v
JSON response / error
      |
      v
Parsed data (or ApiError) returned to caller
```

## 5. Technologies used
- **Python `requests` library** (`pip install requests`) — simple, widely used, easy to extend with retries/sessions later.
- Could swap to **httpx** later if you want async support — structure stays the same either way.

## 6. What's assumed / needs real info
- `BASE_URL` — placeholder `https://api.blazeguard.local/v1`, read from env var `BLAZEGUARD_API_URL`. Replace/set with real backend URL.
- Auth — assumed Bearer token read from env var `BLAZEGUARD_TOKEN`. Update `_get_auth_token()` if BlazeGuard uses cookies/session/OAuth instead.
- Endpoint paths (`/status`, `/alerts`, `/scan`, `/auth/login`) — **placeholders**. Replace with real routes from Blazeguard backend docs/Swagger.

## 7. Files in this prototype
| File | Purpose |
|---|---|
| `api_client.py` | Core GET/POST/PUT/DELETE engine, error handling, auth headers |
| `blazeguard_endpoints.py` | Named functions frontend/consumer code actually calls |
| `example_usage.py` | Demo of how the layer would be used, tested and runs successfully |

**Requires:** `pip install requests`

## Next step
Send over the real BlazeGuard backend routes (or Swagger/Postman collection) and I'll swap the placeholder paths in `blazeguardEndpoints.js` for the real ones.
