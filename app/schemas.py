from pydantic import BaseModel, Field
from typing import Optional, Any, Dict


class PaymentRequest(BaseModel):
    amount: float = Field(..., gt=0, description="Payment amount — must be positive")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO 4217 currency code")

    model_config = {
        "json_schema_extra": {
            "example": {"amount": 100.00, "currency": "GHS"}
        }
    }


class PaymentResponse(BaseModel):
    status: str
    message: str
    transaction_id: str
    amount: float
    currency: str
    processed_at: float


class TransactionRecord(BaseModel):
    key: str
    status: str
    created_at: float
    expires_at: float
    response: Optional[Dict[str, Any]]
