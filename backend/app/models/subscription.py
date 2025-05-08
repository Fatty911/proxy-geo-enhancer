from pydantic import BaseModel, HttpUrl
from typing import List, Optional

class SubscriptionRequest(BaseModel):
    urls: List[HttpUrl] # FastAPI will validate these are URLs
    output_format: str = "clash" # Default to clash

class SubscriptionResponse(BaseModel):
    new_subscription_content: str
    new_subscription_url: Optional[str] = None # If you decide to host the generated content temporarily
    message: Optional[str] = None