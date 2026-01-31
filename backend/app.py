from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import boto3
from datetime import datetime, timedelta
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],

# Prometheus metrics
REQUEST_COUNT = Counter("aws_dashboard_requests_total", "Total API requests", ["endpoint"])
REQUEST_LATENCY = Histogram("aws_dashboard_request_latency_seconds", "Request latency", ["endpoint"])

class AWSCreds(BaseModel):
    access_key: str
    secret_key: str
    region: str = "us-east-1"

def get_clients(creds: AWSCreds):
    session = boto3.Session(
        aws_access_key_id=creds.access_key,
        aws_secret_access_key=creds.secret_key,
        region_name=creds.region,
    )
    return {
        "ec2": session.client("ec2"),
        "s3": session.client("s3"),
        "ce": session.client("ce"),
    }

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    endpoint = request.url.path
    REQUEST_COUNT.labels(endpoint=endpoint).inc()
    with REQUEST_LATENCY.labels(endpoint=endpoint).time():
        response = await call_next(request)
    return response

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}

@app.post("/resources")
def get_resources(creds: AWSCreds):
    try:
        clients = get_clients(creds)
        ec2 = clients["ec2"]
        s3 = clients["s3"]

        instances = ec2.describe_instances()
        instance_count = sum(len(res["Instances"]) for res in instances["Reservations"])

        buckets = s3.list_buckets()
        bucket_count = len(buckets["Buckets"])

        return {
            "ec2_instances": instance_count,
            "s3_buckets": bucket_count
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/cost")
def get_cost(creds: AWSCreds):
    try:
        clients = get_clients(creds)
        ce = clients["ce"]

        end = datetime.today().date()
        start = end - timedelta(days=30)

        response = ce.get_cost_and_usage(
            TimePeriod={"Start": start.strftime("%Y-%m-%d"), "End": end.strftime("%Y-%m-%d")},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}]
        )

        costs = {}
        for group in response["ResultsByTime"][0]["Groups"]:
            service = group["Keys"][0]
            amount = group["Metrics"]["UnblendedCost"]["Amount"]
            costs[service] = round(float(amount), 2)

        return costs
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
