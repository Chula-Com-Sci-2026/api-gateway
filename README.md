# API Gateway / BFF

The browser-facing API for Can't Run Alone. It routes HTTP requests to backend services, validates JWT bearer tokens, aggregates dashboard data, and translates buddy discovery requests from REST to gRPC. The gateway does not require its own database.

## Run with Docker

Install Docker, then run the following commands from this repository's root directory. The image includes Python 3.13, pinned dependencies, and generated gRPC client code.

Create a `.env` file with the backend addresses for your environment:

```dotenv
JWT_SECRET=replace-with-the-shared-backend-jwt-secret
DISCOVERY_TARGET=buddy-discovery:50051
IDENTITY_URL=http://identity:8000
PROFILE_URL=http://profile:8000
SCHEDULING_URL=http://scheduling:8000
CONDITIONS_URL=http://conditions:8000
NOTIFICATION_URL=http://notification:8000
REPUTATION_URL=http://reputation:8000
TRUST_SAFETY_URL=http://trust-safety:8000
FRONTEND_ORIGINS=http://localhost:5173,http://localhost:5174
```

These are example service names and ports. Replace them with addresses reachable from the gateway container. `DISCOVERY_TARGET` uses `host:port`; HTTP service URLs include the scheme and omit the `/api/v1` path. Use the same JWT signing secret as the backend services. `FRONTEND_ORIGINS` is a comma-separated list of allowed browser origins. The `.env` file is ignored by Git.

Build and start the gateway:

```sh
docker build -t cra-gateway .
docker run --rm --name cra-gateway --env-file .env -p 127.0.0.1:8000:8000 cra-gateway
```

If the backend services run on a Docker network, add `--network <network-name>` to the `docker run` command so their service names resolve. The gateway starts independently, but requests to backend services require those services to be running and reachable.

Open <http://localhost:8000/docs> for interactive API documentation. Check <http://localhost:8000/up> for gateway health.

## API

The OpenAPI contract is available at `/openapi.json`. Protected routes validate bearer tokens; buddy request operations derive the caller's identity from the token. Backend services enforce their domain authorization rules.

The `/up` endpoint checks gateway health. If the optional `DATABASE_URL` environment variable is set, it also checks that database connection. A database is not needed for normal gateway operation.

## Tests

Build the image as shown above, then run the gateway tests:

```sh
docker run --rm -e JWT_SECRET=gateway-test-secret-only -e DISCOVERY_TARGET=localhost:50051 cra-gateway pytest -q
```

The tests use a test-only signing secret and stub discovery calls. They do not require a running backend or database.
