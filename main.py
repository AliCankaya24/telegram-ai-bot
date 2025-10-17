from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

app = FastAPI()

@app.get("/")
def root():
    return PlainTextResponse("OK")

@app.get("/health")
def health():
    return JSONResponse({"status": "healthy", "catalog_size": 0, "source": "smoketest"})
