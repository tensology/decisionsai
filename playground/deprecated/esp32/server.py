from fastapi import FastAPI, Body
from pydantic import BaseModel
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

class Message(BaseModel):
    message: str

@app.get("/api/lyra/")
async def lyra_get_endpoint():
    logger.info("GET request received")
    return {"message": "Hello from the Lyra API!"}

@app.post("/api/lyra/")
async def lyra_post_endpoint(message: Message):
    logger.info(f"POST request received with data: {message.dict()}")
    return {"received_message": message.message}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
