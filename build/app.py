from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from kubernetes import client, config
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from prometheus_client.core import CollectorRegistry
import time

app = FastAPI()

# Namespace and label selector configuration
NAMESPACE = "default"
LABEL_SELECTOR = "app=rollouts-demo"

# Kubernetes client initialization
config.load_incluster_config()
v1 = client.CoreV1Api()

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Prometheus metrics
registry = CollectorRegistry()
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "service", "status_code"],
    registry=registry,
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint", "service"],
    registry=registry,
)

@app.middleware("http")
async def add_metrics_middleware(request: Request, call_next):
    """
    Middleware to capture request metrics for Prometheus.
    """
    start_time = time.time()
    service_name = request.headers.get("x-forwarded-service", "unknown")
    method = request.method
    endpoint = request.url.path

    try:
        response = await call_next(request)
        status_code = response.status_code
    except HTTPException as exc:
        status_code = exc.status_code
        REQUEST_COUNT.labels(method, endpoint, service_name, status_code).inc()
        REQUEST_LATENCY.labels(method, endpoint, service_name).observe(time.time() - start_time)
        raise
    except Exception:
        status_code = 500
        REQUEST_COUNT.labels(method, endpoint, service_name, status_code).inc()
        REQUEST_LATENCY.labels(method, endpoint, service_name).observe(time.time() - start_time)
        raise
    else:
        REQUEST_COUNT.labels(method, endpoint, service_name, status_code).inc()
        REQUEST_LATENCY.labels(method, endpoint, service_name).observe(time.time() - start_time)

    return response

@app.get("/metrics")
def metrics():
    """
    Expose Prometheus metrics at the /metrics endpoint.
    """
    metrics_data = generate_latest(registry)
    return Response(content=metrics_data, media_type=CONTENT_TYPE_LATEST)

@app.get("/")
async def read_index():
    """
    Serve the static index.html file.
    """
    return FileResponse("static/index.html")

@app.get("/error")
async def trigger_error():
    """
    This endpoint intentionally triggers a 5XX error for testing purposes.
    """
    raise HTTPException(status_code=500, detail="Internal Server Error")

@app.get("/pods")
async def get_pods():
    """
    Fetch pods in the specified namespace based on the label selector.
    Returns the pod names and associated labels (e.g., v1/v2, blue/green).
    """
    pods = v1.list_namespaced_pod(NAMESPACE, label_selector=LABEL_SELECTOR)
    pod_info = []
    for pod in pods.items:
        container_image = pod.spec.containers[0].image
        image_label = container_image.split(":")[-1]
        pod_info.append({"name": pod.metadata.name, "version": image_label})

    return JSONResponse(content={"pods": pod_info})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
