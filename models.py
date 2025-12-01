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

# ============= TICKET SYSTEM =============
class TicketTransaction(Base):
    __tablename__ = "ticket_transactions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    wallet_raw = Column(String, nullable=False)
    
    # Transaction details
    transaction_type = Column(String)  # 'purchase', 'spent', 'refund', 'bonus'
    amount = Column(Integer, nullable=False)  # Number of tickets (+/-)
    ton_amount = Column(Float, nullable=True)  # TON spent/earned (if applicable)
    
    # Metadata
    description = Column(String)
    transaction_hash = Column(String, nullable=True, index=True)  # Blockchain TX hash
    created_at = Column(DateTime, default=datetime.utcnow)

# ============= GAME EVENTS =============
class EventStatus(str, enum.Enum):
    UPCOMING = "upcoming"
    ACTIVE = "active"
    ENDED = "ended"
    CANCELLED = "cancelled"

class GameEvent(Base):
    __tablename__ = "game_events"
    
    id = Column(Integer, primary_key=True, index=True)
    event_name = Column(String, nullable=False)
    game_type = Column(String, nullable=False)  # 'aim', 'color', 'reflex'
    
    # Timing
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Entry requirements
    ticket_cost = Column(Integer, default=1)
    max_participants = Column(Integer, nullable=True)  # NULL = unlimited
    
    # Prize pool
    prize_pool_ton = Column(Float, default=0.0)
    prize_distribution = Column(JSON, default={
        "1st": 0.50,  # 50% to winner
        "2nd": 0.30,  # 30% to second
        "3rd": 0.20   # 20% to third
    })
    
    # Status
    status = Column(Enum(EventStatus), default=EventStatus.UPCOMING)
    total_participants = Column(Integer, default=0)
    is_paid_out = Column(Boolean, default=False)

# ============= EVENT PARTICIPATION =============
class EventParticipation(Base):
    __tablename__ = "event_participations"
    
    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("game_events.id", ondelete="CASCADE"))
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    wallet_raw = Column(String, nullable=False)
    
    # Entry
    joined_at = Column(DateTime, default=datetime.utcnow)
    tickets_spent = Column(Integer, default=1)
    
    # Results
    best_score = Column(Float, nullable=True)
    final_rank = Column(Integer, nullable=True)
    prize_won = Column(Float, default=0.0)
    is_paid = Column(Boolean, default=False)

# ============= GAME RESULTS (Enhanced) =============
class GameResult(Base):
    __tablename__ = "game_results"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    wallet_raw = Column(String, nullable=False)
    event_id = Column(Integer, ForeignKey("game_events.id", ondelete="SET NULL"), nullable=True)
    
    # Game details
    game_name = Column(String, nullable=False)
    score = Column(Float, nullable=False)
    game_data = Column(JSON, nullable=True)  # Full game session data
    played_at = Column(DateTime, default=datetime.utcnow)
    
    # Anti-cheat
    is_validated = Column(Boolean, default=False)
    is_suspicious = Column(Boolean, default=False)
    validation_notes = Column(String, nullable=True)

# ============= TICKET PACKAGES =============
class TicketPackage(Base):
    __tablename__ = "ticket_packages"
    
    id = Column(Integer, primary_key=True, index=True)
    package_name = Column(String, nullable=False)
    ticket_amount = Column(Integer, nullable=False)
    price_ton = Column(Float, nullable=False)
    bonus_tickets = Column(Integer, default=0)  # Extra tickets as bonus
    is_active = Column(Boolean, default=True)
    display_order = Column(Integer, default=0)
