> ## Documentation Index
>
> Fetch the complete documentation index at: [/llms.txt](https://docs.langchain.com/llms.txt)
>
> Use this file to discover all available pages before exploring further.

[Skip to main content](https://docs.langchain.com/langsmith/deploy-standalone-server#content-area)

[Docs by LangChain home page![light logo](https://mintcdn.com/langchain-5e9cc07a/nQm-sjd_MByLhgeW/images/brand/langchain-docs-dark-blue.png?fit=max&auto=format&n=nQm-sjd_MByLhgeW&q=85&s=5babf1a1962208fd7eed942fa2432ecb)![dark logo](https://mintcdn.com/langchain-5e9cc07a/nQm-sjd_MByLhgeW/images/brand/langchain-docs-light-blue.png?fit=max&auto=format&n=nQm-sjd_MByLhgeW&q=85&s=0bcd2a1f2599ed228bcedf0f535b45b1)](https://docs.langchain.com/)

Deploy

Search...

Ctrl K

- [Ask AI](https://chat.langchain.com/)
- [GitHub](https://github.com/langchain-ai)
- [Try LangSmith](https://smith.langchain.com/)
- [Try LangSmith](https://smith.langchain.com/)

Search...

Navigation

Self-host standalone servers

[Get started](https://docs.langchain.com/langsmith/deployment) [Develop agents](https://docs.langchain.com/langsmith/develop-agents-overview) [Deploy to Cloud](https://docs.langchain.com/langsmith/deploy-to-cloud-overview) [Deploy to Self-hosted](https://docs.langchain.com/langsmith/deploy-to-self-hosted-overview) [Sandboxes](https://docs.langchain.com/langsmith/sandboxes) [Reference](https://docs.langchain.com/langsmith/deploy-reference-overview)

- [Self-hosted overview](https://docs.langchain.com/langsmith/deploy-to-self-hosted-overview)

- [With control plane](https://docs.langchain.com/langsmith/deploy-with-control-plane)

- [Hybrid](https://docs.langchain.com/langsmith/hybrid)

- [Standalone servers](https://docs.langchain.com/langsmith/deploy-standalone-server)

### Configure

- [Platform features](https://docs.langchain.com/langsmith/self-hosted-platform-features)
- [Agent Server scaling](https://docs.langchain.com/langsmith/agent-server-scale)
- [Troubleshooting](https://docs.langchain.com/langsmith/diagnostics-self-hosted)

### Reference

- [Environment variables](https://docs.langchain.com/langsmith/env-var-self-hosted)

## On this page

- [Overview](https://docs.langchain.com/langsmith/deploy-standalone-server#overview)
  - [Workflow](https://docs.langchain.com/langsmith/deploy-standalone-server#workflow)
  - [Supported compute platforms](https://docs.langchain.com/langsmith/deploy-standalone-server#supported-compute-platforms)
- [Prerequisites](https://docs.langchain.com/langsmith/deploy-standalone-server#prerequisites)
- [Kubernetes](https://docs.langchain.com/langsmith/deploy-standalone-server#kubernetes)
- [Docker](https://docs.langchain.com/langsmith/deploy-standalone-server#docker)
- [Docker Compose](https://docs.langchain.com/langsmith/deploy-standalone-server#docker-compose)

# Self-host standalone servers

Copy page

Deploy standalone Agent Servers using Docker, Docker Compose, or Kubernetes without the LangSmith control plane.

Copy page

This guide shows you how to deploy standalone [Agent Servers](https://docs.langchain.com/langsmith/agent-server) directly, without a [control plane](https://docs.langchain.com/langsmith/control-plane). You can deploy the server independently and still send traces to LangSmith ( [self-hosted](https://docs.langchain.com/langsmith/self-hosted) or [Cloud](https://docs.langchain.com/langsmith/cloud)) for [observability](https://docs.langchain.com/langsmith/observability) and [evaluation](https://docs.langchain.com/langsmith/evaluation). Standalone servers are production-ready and provide the most lightweight option for running agents.

## [​](https://docs.langchain.com/langsmith/deploy-standalone-server\#overview)  Overview

You manage a simplified data plane made up of Agent Servers and their required backing services (PostgreSQL, Redis, etc.):

| Component | Responsibilities | Where it runs | Who manages it |
| --- | --- | --- | --- |
| **Control plane** | n/a | n/a | n/a |
| **Data plane** | - Agent Servers<br>- Postgres, Redis, etc. | Your infrastructure | You |

This option gives you full control over scaling, deployment, and CI/CD pipelines, while still allowing optional integration with LangSmith for tracing and evaluation.

Do not run standalone servers in serverless environments. Scale-to-zero may cause task loss and scaling up will not work reliably.

![Standalone server architecture](https://mintcdn.com/langchain-5e9cc07a/Mwtbhvs2R50foe4Y/langsmith/images/standalone-server-light.png?fit=max&auto=format&n=Mwtbhvs2R50foe4Y&q=85&s=db67e2add4cf039b1ce2324fa1c1f244)![Standalone server architecture](https://mintcdn.com/langchain-5e9cc07a/Mwtbhvs2R50foe4Y/langsmith/images/standalone-server-dark.png?fit=max&auto=format&n=Mwtbhvs2R50foe4Y&q=85&s=57ede6682332db867f1900200f675a5f)

### [​](https://docs.langchain.com/langsmith/deploy-standalone-server\#workflow)  Workflow

1. Define and test your graph locally using the `langgraph-cli` or [Studio](https://docs.langchain.com/langsmith/studio).
2. Package your agent as a Docker image.
3. Deploy the Agent Server to your compute platform of choice (Kubernetes, Docker, VM).
4. Optionally, configure LangSmith API keys and endpoints so the server reports traces and evaluations back to LangSmith (self-hosted or SaaS).

### [​](https://docs.langchain.com/langsmith/deploy-standalone-server\#supported-compute-platforms)  Supported compute platforms

- **Kubernetes**: Use the LangSmith Helm chart to run Agent Servers in a Kubernetes cluster. This is the recommended option for production-grade deployments.
- **Docker**: Run in any Docker-supported compute platform (local dev machine, VM, ECS, etc.). This is best suited for development or small-scale workloads.

## [​](https://docs.langchain.com/langsmith/deploy-standalone-server\#prerequisites)  Prerequisites

1. Use the [LangGraph CLI](https://docs.langchain.com/langsmith/cli) to [test your application locally](https://docs.langchain.com/langsmith/local-dev-testing).
2. Use the [LangGraph CLI](https://docs.langchain.com/langsmith/cli) to build a Docker image (i.e. `langgraph build`).
3. The following environment variables are needed for a data plane deployment.
4. `REDIS_URI`: Connection details to a Redis instance. Redis will be used as a pub-sub broker to enable streaming real time output from background runs. The value of `REDIS_URI` must be a valid [Redis connection URI](https://redis-py.readthedocs.io/en/stable/connections.html#redis.Redis.from_url).






**Shared Redis Instance**
Multiple self-hosted deployments can share the same Redis instance. For example, for `Deployment A`, `REDIS_URI` can be set to `redis://<hostname_1>:<port>/1` and for `Deployment B`, `REDIS_URI` can be set to `redis://<hostname_1>:<port>/2`.`1` and `2` are different database numbers within the same instance, but `<hostname_1>` is shared. **The same database number cannot be used for separate deployments**.

5. `DATABASE_URI`: Postgres connection details. Postgres will be used to store assistants, threads, runs, persist thread state and long term memory, and to manage the state of the background task queue with ‘exactly once’ semantics. The value of `DATABASE_URI` must be a valid [Postgres connection URI](https://www.postgresql.org/docs/current/libpq-connect.html#LIBPQ-CONNSTRING-URIS).






**Shared Postgres Instance**
Multiple self-hosted deployments can share the same Postgres instance. For example, for `Deployment A`, `DATABASE_URI` can be set to `postgres://<user>:<password>@/<database_name_1>?host=<hostname_1>` and for `Deployment B`, `DATABASE_URI` can be set to `postgres://<user>:<password>@/<database_name_2>?host=<hostname_1>`.`<database_name_1>` and `database_name_2` are different databases within the same instance, but `<hostname_1>` is shared. **The same database cannot be used for separate deployments**.









You can optionally store checkpoint data in MongoDB instead of PostgreSQL. PostgreSQL is still required for all other server data. See [Configure checkpointer backend](https://docs.langchain.com/langsmith/configure-checkpointer) for details.

6. `LANGSMITH_API_KEY`: LangSmith API key.
7. `LANGGRAPH_CLOUD_LICENSE_KEY`: LangSmith license key. This will be used to authenticate ONCE at server start up.
8. `LANGSMITH_ENDPOINT`: To send traces to a [self-hosted LangSmith](https://docs.langchain.com/langsmith/self-hosted) instance, set `LANGSMITH_ENDPOINT` to the hostname of the self-hosted LangSmith instance.
9. Egress to `https://beacon.langchain.com` from your network. This is required for license verification and usage reporting if not running in air-gapped mode. See the [Egress documentation](https://docs.langchain.com/langsmith/self-host-egress) for more details.

## [​](https://docs.langchain.com/langsmith/deploy-standalone-server\#kubernetes)  Kubernetes

Use this [Helm chart](https://github.com/langchain-ai/helm/blob/main/charts/langgraph-cloud/README.md) to deploy an Agent Server to a Kubernetes cluster. This is the recommended setup for production standalone server deployments.The Helm chart (v0.2.6+) supports MongoDB checkpointing with a bundled instance (dev/testing) or an external deployment (production). Set `mongo.enabled: true` in your values file. See [Configure checkpointer backend](https://docs.langchain.com/langsmith/configure-checkpointer#deploy-by-environment) for full configuration details.

## [​](https://docs.langchain.com/langsmith/deploy-standalone-server\#docker)  Docker

This `docker` example is intended for local development and testing.Run the following `docker` command:

```
docker run \
    --env-file .env \
    -p 8123:8000 \
    -e REDIS_URI="foo" \
    -e DATABASE_URI="bar" \
    -e LANGSMITH_API_KEY="baz" \
    my-image
```

- You need to replace `my-image` with the name of the image you built in the prerequisite steps (from `langgraph build`)

and you should provide appropriate values for `REDIS_URI`, `DATABASE_URI`, and `LANGSMITH_API_KEY`.

- If your application requires additional environment variables, you can pass them in a similar way.

## [​](https://docs.langchain.com/langsmith/deploy-standalone-server\#docker-compose)  Docker Compose

This Docker Compose example is intended for local development and testing.Use the following Docker Compose file:

```
volumes:
    langgraph-data:
        driver: local
services:
    langgraph-redis:
        image: redis:6
        healthcheck:
            test: redis-cli ping
            interval: 5s
            timeout: 1s
            retries: 5
    langgraph-postgres:
        image: postgres:16
        ports:
            - "5432:5432"
        environment:
            POSTGRES_DB: postgres
            POSTGRES_USER: postgres
            POSTGRES_PASSWORD: postgres
        volumes:
            - langgraph-data:/var/lib/postgresql/data
        healthcheck:
            test: pg_isready -U postgres
            start_period: 10s
            timeout: 1s
            retries: 5
            interval: 5s
    langgraph-api:
        image: ${IMAGE_NAME}
        ports:
            - "8123:8000"
        depends_on:
            langgraph-redis:
                condition: service_healthy
            langgraph-postgres:
                condition: service_healthy
        env_file:
            - .env
        environment:
            REDIS_URI: redis://langgraph-redis:6379
            LANGSMITH_API_KEY: ${LANGSMITH_API_KEY}
            DATABASE_URI: postgres://postgres:postgres@langgraph-postgres:5432/postgres?sslmode=disable
```

Run `docker compose up` with this file in the same folder.

With MongoDB checkpointing

To store checkpoints in MongoDB instead of PostgreSQL, add a MongoDB service and configure the checkpointer backend. Set the backend to `"mongo"` in your `langgraph.json` or use the `LS_DEFAULT_CHECKPOINTER_BACKEND` environment variable. PostgreSQL is still required for all other server data.

```
volumes:
    langgraph-data:
        driver: local
    langgraph-mongo-data:
        driver: local
services:
    langgraph-redis:
        image: redis:6
        healthcheck:
            test: redis-cli ping
            interval: 5s
            timeout: 1s
            retries: 5
    langgraph-postgres:
        image: postgres:16
        ports:
            - "5432:5432"
        environment:
            POSTGRES_DB: postgres
            POSTGRES_USER: postgres
            POSTGRES_PASSWORD: postgres
        volumes:
            - langgraph-data:/var/lib/postgresql/data
        healthcheck:
            test: pg_isready -U postgres
            start_period: 10s
            timeout: 1s
            retries: 5
            interval: 5s
    langgraph-mongo:
        image: mongo:7
        command: ["mongod", "--replSet", "rs0"]
        ports:
            - "27017:27017"
        volumes:
            - langgraph-mongo-data:/data/db
        healthcheck:
            test: mongosh --eval "try { rs.status().ok } catch(e) { rs.initiate({_id:'rs0',members:[{_id:0,host:'langgraph-mongo:27017'}]}).ok }" --quiet
            interval: 5s
            timeout: 10s
            retries: 10
            start_period: 10s
    langgraph-api:
        image: ${IMAGE_NAME}
        ports:
            - "8123:8000"
        depends_on:
            langgraph-redis:
                condition: service_healthy
            langgraph-postgres:
                condition: service_healthy
            langgraph-mongo:
                condition: service_healthy
        env_file:
            - .env
        environment:
            REDIS_URI: redis://langgraph-redis:6379
            LANGSMITH_API_KEY: ${LANGSMITH_API_KEY}
            DATABASE_URI: postgres://postgres:postgres@langgraph-postgres:5432/postgres?sslmode=disable
            LS_DEFAULT_CHECKPOINTER_BACKEND: mongo
            LS_MONGODB_URI: mongodb://langgraph-mongo:27017/langgraph?replicaSet=rs0
```

See [Configure checkpointer backend](https://docs.langchain.com/langsmith/configure-checkpointer) for more details on MongoDB configuration options.

This will launch an Agent Server on port `8123` (change the port mapping in `langgraph-api` if needed). Test if the application is healthy:

```
curl --request GET --url 0.0.0.0:8123/ok
```

Assuming everything is running correctly, you should see a response like:

```
{"ok":true}
```

* * *

[Connect these docs](https://docs.langchain.com/use-these-docs) to Claude, VSCode, and more via MCP for real-time answers.

[Edit this page on GitHub](https://github.com/langchain-ai/docs/edit/main/src/langsmith/deploy-standalone-server.mdx) or [file an issue](https://github.com/langchain-ai/docs/issues/new/choose).

Was this page helpful?

YesNo

[Hybrid\\
\\
Previous](https://docs.langchain.com/langsmith/hybrid) [Self-hosted platform features\\
\\
Next](https://docs.langchain.com/langsmith/self-hosted-platform-features)

Ctrl+I

[Docs by LangChain home page![light logo](https://mintcdn.com/langchain-5e9cc07a/nQm-sjd_MByLhgeW/images/brand/langchain-docs-dark-blue.png?fit=max&auto=format&n=nQm-sjd_MByLhgeW&q=85&s=5babf1a1962208fd7eed942fa2432ecb)![dark logo](https://mintcdn.com/langchain-5e9cc07a/nQm-sjd_MByLhgeW/images/brand/langchain-docs-light-blue.png?fit=max&auto=format&n=nQm-sjd_MByLhgeW&q=85&s=0bcd2a1f2599ed228bcedf0f535b45b1)](https://docs.langchain.com/)

[github](https://github.com/langchain-ai) [x](https://x.com/LangChain) [linkedin](https://www.linkedin.com/company/langchain) [youtube](https://www.youtube.com/@LangChain)

Resources

[Forum](https://forum.langchain.com/) [Changelog](https://changelog.langchain.com/) [LangChain Academy](https://academy.langchain.com/) [Contact Sales](https://www.langchain.com/contact-sales)

Company

[Home](https://langchain.com/) [Trust Center](https://trust.langchain.com/) [Careers](https://langchain.com/careers) [Blog](https://blog.langchain.com/)

[github](https://github.com/langchain-ai) [x](https://x.com/LangChain) [linkedin](https://www.linkedin.com/company/langchain) [youtube](https://www.youtube.com/@LangChain)

## Chat LangChain

[Open chat.langchain.com in a new tab](https://chat.langchain.com/ "Open chat.langchain.com in a new tab")

![Standalone server architecture](https://mintcdn.com/langchain-5e9cc07a/Mwtbhvs2R50foe4Y/langsmith/images/standalone-server-light.png?w=840&fit=max&auto=format&n=Mwtbhvs2R50foe4Y&q=85&s=9e4727b09b6c88780787c6d6ff7bd490)

![Standalone server architecture](https://mintcdn.com/langchain-5e9cc07a/Mwtbhvs2R50foe4Y/langsmith/images/standalone-server-dark.png?w=840&fit=max&auto=format&n=Mwtbhvs2R50foe4Y&q=85&s=68547f892a250fc426ce9f0dad79a80e)