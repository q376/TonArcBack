from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Enum, ForeignKey, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from datetime import datetime
import enum

Base = declarative_base()

# ============= USER MODEL =============
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    wallet_raw = Column(String, unique=True, index=True, nullable=False)
    wallet_user_friendly = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Balance & Stats
    ticket_balance = Column(Integer, default=0)  # Available tickets
    total_earned = Column(Float, default=0.0)    # Total TON earned
    tournaments_won = Column(Integer, default=0)
    games_played = Column(Integer, default=0)
    
    # Flags
    is_banned = Column(Boolean, default=False)
    ban_reason = Column(String, nullable=True)
