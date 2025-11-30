from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Dict, Any

class WalletAuth(BaseModel):
    wallet_raw: str
    wallet_user_friendly: str

class TicketPurchaseRequest(BaseModel):
    wallet: str
    package_id: int
    transaction_hash: str

class EventCreateRequest(BaseModel):
    event_name: str
    game_type: str
    start_time: datetime
    duration_minutes: int
    ticket_cost: int
    prize_pool_ton: float
    max_participants: Optional[int] = None

class EventJoinRequest(BaseModel):
    wallet: str
    event_id: int

class ScoreSubmission(BaseModel):
    wallet: str
    event_id: Optional[int] = None
    game: str
    score: float
    gameData: Dict[str, Any]
    timestamp: int

class GameResultResponse(BaseModel):
    id: int
    wallet_raw: str
    game_name: str
    score: float
    played_at: datetime

    class Config:
        from_attributes = True
