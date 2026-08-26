from fastapi import FastAPI

app = FastAPI(title="BlazeGuard")


@app.get("/")
def home():
    return {
        "message": "BlazeGuard is running",
        "status": "active"
    }
