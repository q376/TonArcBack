from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
import redis
import json
import os
import secrets
import hashlib
import time

# ===== APP SETUP =====
app = FastAPI(title="TonArcade API")

# Redis connection
REDIS_URL = os.getenv("REDIS_URL", "redis://red-d5eck9khg0os73988ao0:6379")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "your_admin_secret_here")

if not ADMIN_SECRET:
    raise ValueError("ADMIN_SECRET environment variable is required")

# Initialize Redis
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# Test connection
try:
    redis_client.ping()
    print("✅ Redis connected successfully")
except redis.ConnectionError:
    raise Exception("❌ Failed to connect to Redis")

# ===== PYDANTIC MODELS (Validation Only) =====

class WalletAuth(BaseModel):
    wallet_raw: str
    wallet_user_friendly: str

class TournamentCreateRequest(BaseModel):
    game_type: str
    entry_fee: float
    duration_hours: int
    prize_pool: float = 0
    max_participants: Optional[int] = None
    start_delay_hours: float = 0

class ScoreSubmission(BaseModel):
    wallet: str
    event_id: Optional[int] = None
    game: str
    score: float
    gameData: Dict[str, Any]
    timestamp: int

# ===== HELPER FUNCTIONS =====

def verify_admin(admin_secret: str = Header(None, alias="X-Admin-Secret")):
    if admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    return True

def get_timestamp():
    return int(time.time())

def format_user_data(user_dict):
    """Convert Redis hash to user response format"""
    if not user_dict:
        return None
    return {
        "wallet_raw": user_dict.get("wallet_raw"),
        "wallet_user_friendly": user_dict.get("wallet_friendly"),
        "created_at": datetime.fromtimestamp(int(user_dict.get("created_at", 0))).isoformat(),
        "total_earned": float(user_dict.get("total_earned", 0)),
        "tournaments_won": int(user_dict.get("tournaments_won", 0)),
        "games_played": int(user_dict.get("games_played", 0))
    }

def format_tournament_data(tournament_dict, tournament_id):
    """Convert Redis hash to tournament response format"""
    if not tournament_dict:
        return None
    
    # Get participant count from leaderboard
    participant_count = redis_client.zcard(f"tournament:{tournament_id}:leaderboard")
    
    return {
        "id": int(tournament_id),
        "contract_address": tournament_dict.get("contract_address"),
        "game_type": tournament_dict.get("game_type"),
        "entry_fee_ton": float(tournament_dict.get("entry_fee_ton", 0)),
        "total_pool": float(tournament_dict.get("total_pool", 0)),
        "start_time": datetime.fromtimestamp(int(tournament_dict.get("start_time", 0))).isoformat(),
        "end_time": datetime.fromtimestamp(int(tournament_dict.get("end_time", 0))).isoformat(),
        "status": tournament_dict.get("status"),
        "total_participants": participant_count,
        "max_participants": int(tournament_dict.get("max_participants")) if tournament_dict.get("max_participants") else None,
        "is_full": bool(tournament_dict.get("max_participants") and participant_count >= int(tournament_dict.get("max_participants")))
    }

# ===== CORS =====

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== BASIC ENDPOINTS =====

@app.get("/")
def health_check():
    return {
        "status": "ok",
        "message": "TonArcade API is running (Redis-powered)",
        "redis_connected": redis_client.ping(),
        "endpoints": {
            "auth": "/auth/wallet",
            "tournaments": "/tournaments",
            "admin": "/admin/tournaments/create"
        }
    }

# ===== AUTHENTICATION =====

@app.post("/auth/wallet")
def wallet_login(auth: WalletAuth):
    wallet_raw = auth.wallet_raw.strip()
    wallet_friendly = auth.wallet_user_friendly.strip()
    
    if not wallet_raw or not wallet_friendly:
        raise HTTPException(status_code=400, detail="Wallet addresses cannot be empty")
    
    # Check if user exists
    user_exists = redis_client.sismember("users:all", wallet_raw)
    
    if user_exists:
        # Existing user
        user_data = redis_client.hgetall(f"user:{wallet_raw}")
        return {
            "message": "Login successful",
            "user": format_user_data(user_data)
        }
    
    # New user - create
    redis_client.hset(f"user:{wallet_raw}", mapping={
        "wallet_raw": wallet_raw,
        "wallet_friendly": wallet_friendly,
        "created_at": get_timestamp(),
        "total_earned": 0.0,
        "tournaments_won": 0,
        "games_played": 0,
        "is_banned": "false"
    })
    
    redis_client.sadd("users:all", wallet_raw)
    
    user_data = redis_client.hgetall(f"user:{wallet_raw}")
    
    return {
        "message": "User registered",
        "user": format_user_data(user_data)
    }

# ===== ADMIN ENDPOINTS =====

@app.post("/admin/tournaments/create")
def admin_create_tournament(
    request: TournamentCreateRequest,
    _: bool = Depends(verify_admin)
):
    """Create a new tournament (ADMIN ONLY)"""
    
    # Generate tournament ID
    tournament_id = redis_client.incr("tournament:id_counter")
    
    # Generate contract address
    contract_address = f"EQ{secrets.token_hex(32)}"
    
    # Calculate timestamps (using CURRENT UTC time on server)
    # Client timezone doesn't matter - we calculate relative to NOW
    start_time = get_timestamp() + int(request.start_delay_hours * 3600)
    end_time = start_time + (request.duration_hours * 3600)
    
    # Determine initial status
    status = "upcoming" if request.start_delay_hours > 0 else "active"
    
    # Store tournament
    redis_client.hset(f"tournament:{tournament_id}", mapping={
        "id": tournament_id,
        "contract_address": contract_address,
        "game_type": request.game_type,
        "entry_fee_ton": request.entry_fee,
        "total_pool": request.prize_pool,
        "initial_prize_pool": request.prize_pool,
        "start_time": start_time,
        "end_time": end_time,
        "created_at": get_timestamp(),
        "max_participants": request.max_participants or "",
        "status": status,
        "is_finalized": "false"
    })
    
    # Add to indexes
    redis_client.sadd("tournaments:all", tournament_id)
    redis_client.sadd(f"tournaments:{status}", tournament_id)
    redis_client.sadd(f"tournament:by_game:{request.game_type}", tournament_id)
    
    return {
        "tournament_id": tournament_id,
        "contract_address": contract_address,
        "game_type": request.game_type,
        "entry_fee": request.entry_fee,
        "start_time": datetime.fromtimestamp(start_time).isoformat(),
        "end_time": datetime.fromtimestamp(end_time).isoformat(),
        "status": status,
        "message": "Tournament created successfully"
    }

@app.get("/admin/tournaments/{tournament_id}/status")
def admin_get_tournament_status(
    tournament_id: int,
    _: bool = Depends(verify_admin)
):
    """Get detailed tournament status (ADMIN ONLY)"""
    
    tournament_data = redis_client.hgetall(f"tournament:{tournament_id}")
    
    if not tournament_data:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    # Get top 10 scores
    top_scores = redis_client.zrevrange(
        f"tournament:{tournament_id}:leaderboard",
        0, 9,
        withscores=True
    )
    
    formatted_scores = []
    for wallet, score in top_scores:
        score_meta = redis_client.hgetall(f"score:{tournament_id}:{wallet}")
        formatted_scores.append({
            "wallet_raw": wallet,
            "score": score,
            "submitted_at": datetime.fromtimestamp(int(score_meta.get("submitted_at", 0))).isoformat() if score_meta else None,
            "is_validated": score_meta.get("is_validated", "false") == "true" if score_meta else False
        })
    
    return {
        "tournament": format_tournament_data(tournament_data, tournament_id),
        "top_scores": formatted_scores
    }

@app.post("/admin/tournaments/{tournament_id}/finalize")
def admin_finalize_tournament(
    tournament_id: int,
    _: bool = Depends(verify_admin)
):
    """Finalize tournament and generate signed payouts (ADMIN ONLY)"""
    
    tournament_data = redis_client.hgetall(f"tournament:{tournament_id}")
    
    if not tournament_data:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    if tournament_data.get("is_finalized") == "true":
        raise HTTPException(status_code=400, detail="Tournament already finalized")
    
    # Get top 3 scores
    top_scores = redis_client.zrevrange(
        f"tournament:{tournament_id}:leaderboard",
        0, 2,
        withscores=True
    )
    
    if len(top_scores) == 0:
        raise HTTPException(status_code=400, detail="No valid scores submitted")
    
    total_pool = float(tournament_data.get("total_pool", 0))
    
    # Calculate prize distribution
    if len(top_scores) == 1:
        prizes = {0: total_pool}
    elif len(top_scores) == 2:
        prizes = {0: total_pool * 0.70, 1: total_pool * 0.30}
    else:
        prizes = {0: total_pool * 0.50, 1: total_pool * 0.30, 2: total_pool * 0.20}
    
    payouts = []
    
    for rank, (wallet, score) in enumerate(top_scores):
        prize_amount = prizes.get(rank, 0)
        
        if prize_amount <= 0:
            continue
        
        nonce = secrets.randbits(64)
        
        payload_hash = hashlib.sha256(
            f"{wallet}{prize_amount}{nonce}{tournament_id}".encode()
        ).hexdigest()
        
        payouts.append({
            "rank": rank + 1,
            "wallet": wallet,
            "score": score,
            "prize_ton": prize_amount,
            "payload_hash": payload_hash,
            "nonce": nonce
        })
        
        # Update user stats
        redis_client.hincrbyfloat(f"user:{wallet}", "total_earned", prize_amount)
        if rank == 0:  # Winner
            redis_client.hincrby(f"user:{wallet}", "tournaments_won", 1)
    
    # Update tournament status
    redis_client.hset(f"tournament:{tournament_id}", mapping={
        "status": "completed",
        "is_finalized": "true"
    })
    
    # Move to completed index
    redis_client.srem("tournaments:active", tournament_id)
    redis_client.srem("tournaments:finalizing", tournament_id)
    redis_client.sadd("tournaments:completed", tournament_id)
    
    return {
        "message": "Tournament finalized",
        "tournament_id": tournament_id,
        "total_pool": total_pool,
        "payouts": payouts
    }

@app.post("/admin/tournaments/{tournament_id}/delete")
def admin_delete_tournament(
    tournament_id: int,
    _: bool = Depends(verify_admin)
):
    """Delete/cancel a tournament (ADMIN ONLY)"""
    
    tournament_data = redis_client.hgetall(f"tournament:{tournament_id}")
    
    if not tournament_data:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    # Delete tournament data
    redis_client.delete(f"tournament:{tournament_id}")
    redis_client.delete(f"tournament:{tournament_id}:leaderboard")
    redis_client.delete(f"tournament:{tournament_id}:entries")
    redis_client.delete(f"tournament:{tournament_id}:participants")
    
    # Delete all score metadata
    participants = redis_client.smembers(f"tournament:{tournament_id}:participants")
    for wallet in participants:
        redis_client.delete(f"score:{tournament_id}:{wallet}")
    
    # Remove from indexes
    redis_client.srem("tournaments:all", tournament_id)
    redis_client.srem("tournaments:active", tournament_id)
    redis_client.srem("tournaments:upcoming", tournament_id)
    redis_client.srem("tournaments:completed", tournament_id)
    redis_client.srem("tournaments:finalizing", tournament_id)
    
    game_type = tournament_data.get("game_type")
    if game_type:
        redis_client.srem(f"tournament:by_game:{game_type}", tournament_id)
    
    return {
        "message": "Tournament deleted",
        "tournament_id": tournament_id
    }

# ===== PUBLIC ENDPOINTS =====

@app.get("/tournaments")
def get_tournaments(
    status: Optional[str] = None,
    game_type: Optional[str] = None
):
    """Get all tournaments (PUBLIC)"""
    
    # Get tournament IDs based on filters
    if status:
        tournament_ids = redis_client.smembers(f"tournaments:{status}")
    else:
        tournament_ids = redis_client.smembers("tournaments:all")
    
    if game_type and tournament_ids:
        game_tournaments = redis_client.smembers(f"tournament:by_game:{game_type}")
        tournament_ids = tournament_ids.intersection(game_tournaments)
    
    # Fetch all tournament data using pipeline (efficient batch operation)
    if not tournament_ids:
        return {"tournaments": []}
    
    pipe = redis_client.pipeline()
    for tid in tournament_ids:
        pipe.hgetall(f"tournament:{tid}")
    
    tournaments_data = pipe.execute()
    
    # Format tournaments
    tournaments = []
    for tid, data in zip(tournament_ids, tournaments_data):
        if data:
            formatted = format_tournament_data(data, tid)
            if formatted:
                tournaments.append(formatted)
    
    # Sort by created_at (most recent first)
    tournaments.sort(key=lambda x: x.get("start_time", ""), reverse=True)
    
    return {"tournaments": tournaments}

@app.get("/tournaments/{tournament_id}")
def get_tournament_details(tournament_id: int):
    """Get tournament details with leaderboard (PUBLIC)"""
    
    tournament_data = redis_client.hgetall(f"tournament:{tournament_id}")
    
    if not tournament_data:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    # Get top 20 scores
    scores = redis_client.zrevrange(
        f"tournament:{tournament_id}:leaderboard",
        0, 19,
        withscores=True
    )
    
    leaderboard = []
    for rank, (wallet, score) in enumerate(scores, 1):
        score_meta = redis_client.hgetall(f"score:{tournament_id}:{wallet}")
        
        leaderboard.append({
            "rank": rank,
            "wallet": wallet[:6] + "..." + wallet[-4:],
            "wallet_full": wallet,
            "score": score,
            "submitted_at": datetime.fromtimestamp(int(score_meta.get("submitted_at", 0))).isoformat() if score_meta else None
        })
    
    return {
        "tournament": format_tournament_data(tournament_data, tournament_id),
        "leaderboard": leaderboard
    }

@app.post("/tournaments/{tournament_id}/submit")
def submit_tournament_score(
    tournament_id: int,
    submission: ScoreSubmission
):
    """Submit a score to a tournament (PUBLIC)"""
    
    tournament_data = redis_client.hgetall(f"tournament:{tournament_id}")
    
    if not tournament_data:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    # Check tournament status
    if tournament_data.get("status") != "active":
        raise HTTPException(status_code=400, detail="Tournament is not active")
    
    # Check if tournament has ended
    end_time = int(tournament_data.get("end_time", 0))
    if get_timestamp() > end_time:
        raise HTTPException(status_code=400, detail="Tournament has ended")
    
    # Validate score
    if submission.score <= 0 or submission.score > 999999:
        raise HTTPException(status_code=400, detail="Invalid score")
    
    # Get current score
    current_score = redis_client.zscore(
        f"tournament:{tournament_id}:leaderboard",
        submission.wallet
    )
    
    # Check if score is better
    if current_score is not None and submission.score <= current_score:
        return {
            "message": "Previous score was better",
            "score": current_score
        }
    
    # Update score (GT = only if greater than current)
    redis_client.zadd(
        f"tournament:{tournament_id}:leaderboard",
        {submission.wallet: submission.score},
        gt=True
    )
    
    # Store score metadata
    redis_client.hset(f"score:{tournament_id}:{submission.wallet}", mapping={
        "score": submission.score,
        "game_data": json.dumps(submission.gameData),
        "submitted_at": get_timestamp(),
        "is_validated": "true",
        "is_suspicious": "false"
    })
    
    # If first submission, add to participants
    if current_score is None:
        redis_client.sadd(f"tournament:{tournament_id}:participants", submission.wallet)
        
        # Update user games_played
        redis_client.hincrby(f"user:{submission.wallet}", "games_played", 1)
    
    return {
        "message": "Score submitted successfully" if current_score is None else "Score updated",
        "tournament_id": tournament_id,
        "score": submission.score
    }

# ===== BACKGROUND TASKS =====

@app.get("/admin/tasks/update-statuses")
def update_tournament_statuses(_: bool = Depends(verify_admin)):
    """Update tournament statuses based on time (run periodically)"""
    
    now = get_timestamp()
    updated_count = 0
    
    # Check upcoming tournaments
    upcoming_ids = redis_client.smembers("tournaments:upcoming")
    for tid in upcoming_ids:
        tournament_data = redis_client.hgetall(f"tournament:{tid}")
        start_time = int(tournament_data.get("start_time", 0))
        
        if now >= start_time:
            # Start tournament
            redis_client.hset(f"tournament:{tid}", "status", "active")
            redis_client.srem("tournaments:upcoming", tid)
            redis_client.sadd("tournaments:active", tid)
            updated_count += 1
    
    # Check active tournaments
    active_ids = redis_client.smembers("tournaments:active")
    for tid in active_ids:
        tournament_data = redis_client.hgetall(f"tournament:{tid}")
        end_time = int(tournament_data.get("end_time", 0))
        
        if now >= end_time:
            # End tournament
            redis_client.hset(f"tournament:{tid}", "status", "finalizing")
            redis_client.srem("tournaments:active", tid)
            redis_client.sadd("tournaments:finalizing", tid)
            updated_count += 1
    
    return {
        "message": f"Updated {updated_count} tournaments",
        "timestamp": datetime.now().isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
'''
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
import redis
import json
import os
import secrets
import hashlib
import time

# ===== APP SETUP =====
app = FastAPI(title="TonArcade API")

# Redis connection
REDIS_URL = os.getenv("REDIS_URL", "redis://red-d5eck9khg0os73988ao0:6379")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "your_admin_secret_here")

if not ADMIN_SECRET:
    raise ValueError("ADMIN_SECRET environment variable is required")

# Initialize Redis
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# Test connection
try:
    redis_client.ping()
    print("✅ Redis connected successfully")
except redis.ConnectionError:
    raise Exception("❌ Failed to connect to Redis")

# ===== PYDANTIC MODELS (Validation Only) =====

class WalletAuth(BaseModel):
    wallet_raw: str
    wallet_user_friendly: str

class TournamentCreateRequest(BaseModel):
    game_type: str
    entry_fee: float
    duration_hours: int
    prize_pool: float = 0
    max_participants: Optional[int] = None
    start_delay_hours: float = 0

class ScoreSubmission(BaseModel):
    wallet: str
    event_id: Optional[int] = None
    game: str
    score: float
    gameData: Dict[str, Any]
    timestamp: int

# ===== HELPER FUNCTIONS =====

def verify_admin(admin_secret: str = Header(None, alias="X-Admin-Secret")):
    if admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    return True

def get_timestamp():
    return int(time.time())

def format_user_data(user_dict):
    """Convert Redis hash to user response format"""
    if not user_dict:
        return None
    return {
        "wallet_raw": user_dict.get("wallet_raw"),
        "wallet_user_friendly": user_dict.get("wallet_friendly"),
        "created_at": datetime.fromtimestamp(int(user_dict.get("created_at", 0))).isoformat(),
        "total_earned": float(user_dict.get("total_earned", 0)),
        "tournaments_won": int(user_dict.get("tournaments_won", 0)),
        "games_played": int(user_dict.get("games_played", 0))
    }

def format_tournament_data(tournament_dict, tournament_id):
    """Convert Redis hash to tournament response format"""
    if not tournament_dict:
        return None
    
    # Get participant count from leaderboard
    participant_count = redis_client.zcard(f"tournament:{tournament_id}:leaderboard")
    
    return {
        "id": int(tournament_id),
        "contract_address": tournament_dict.get("contract_address"),
        "game_type": tournament_dict.get("game_type"),
        "entry_fee_ton": float(tournament_dict.get("entry_fee_ton", 0)),
        "total_pool": float(tournament_dict.get("total_pool", 0)),
        "start_time": datetime.fromtimestamp(int(tournament_dict.get("start_time", 0))).isoformat(),
        "end_time": datetime.fromtimestamp(int(tournament_dict.get("end_time", 0))).isoformat(),
        "status": tournament_dict.get("status"),
        "total_participants": participant_count,
        "max_participants": int(tournament_dict.get("max_participants")) if tournament_dict.get("max_participants") else None,
        "is_full": bool(tournament_dict.get("max_participants") and participant_count >= int(tournament_dict.get("max_participants")))
    }

# ===== CORS =====

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== BASIC ENDPOINTS =====

@app.get("/")
def health_check():
    return {
        "status": "ok",
        "message": "TonArcade API is running (Redis-powered)",
        "redis_connected": redis_client.ping(),
        "endpoints": {
            "auth": "/auth/wallet",
            "tournaments": "/tournaments",
            "admin": "/admin/tournaments/create"
        }
    }

# ===== AUTHENTICATION =====

@app.post("/auth/wallet")
def wallet_login(auth: WalletAuth):
    wallet_raw = auth.wallet_raw.strip()
    wallet_friendly = auth.wallet_user_friendly.strip()
    
    if not wallet_raw or not wallet_friendly:
        raise HTTPException(status_code=400, detail="Wallet addresses cannot be empty")
    
    # Check if user exists
    user_exists = redis_client.sismember("users:all", wallet_raw)
    
    if user_exists:
        # Existing user
        user_data = redis_client.hgetall(f"user:{wallet_raw}")
        return {
            "message": "Login successful",
            "user": format_user_data(user_data)
        }
    
    # New user - create
    redis_client.hset(f"user:{wallet_raw}", mapping={
        "wallet_raw": wallet_raw,
        "wallet_friendly": wallet_friendly,
        "created_at": get_timestamp(),
        "total_earned": 0.0,
        "tournaments_won": 0,
        "games_played": 0,
        "is_banned": "false"
    })
    
    redis_client.sadd("users:all", wallet_raw)
    
    user_data = redis_client.hgetall(f"user:{wallet_raw}")
    
    return {
        "message": "User registered",
        "user": format_user_data(user_data)
    }

# ===== ADMIN ENDPOINTS =====

@app.post("/admin/tournaments/create")
def admin_create_tournament(
    request: TournamentCreateRequest,
    _: bool = Depends(verify_admin)
):
    """Create a new tournament (ADMIN ONLY)"""
    
    # Generate tournament ID
    tournament_id = redis_client.incr("tournament:id_counter")
    
    # Generate contract address
    contract_address = f"EQ{secrets.token_hex(32)}"
    
    # Calculate timestamps
    start_time = get_timestamp() + int(request.start_delay_hours * 3600)
    end_time = start_time + (request.duration_hours * 3600)
    
    # Determine initial status
    status = "upcoming" if request.start_delay_hours > 0 else "active"
    
    # Store tournament
    redis_client.hset(f"tournament:{tournament_id}", mapping={
        "id": tournament_id,
        "contract_address": contract_address,
        "game_type": request.game_type,
        "entry_fee_ton": request.entry_fee,
        "total_pool": request.prize_pool,
        "initial_prize_pool": request.prize_pool,
        "start_time": start_time,
        "end_time": end_time,
        "created_at": get_timestamp(),
        "max_participants": request.max_participants or "",
        "status": status,
        "is_finalized": "false"
    })
    
    # Add to indexes
    redis_client.sadd("tournaments:all", tournament_id)
    redis_client.sadd(f"tournaments:{status}", tournament_id)
    redis_client.sadd(f"tournament:by_game:{request.game_type}", tournament_id)
    
    return {
        "tournament_id": tournament_id,
        "contract_address": contract_address,
        "game_type": request.game_type,
        "entry_fee": request.entry_fee,
        "end_time": datetime.fromtimestamp(end_time).isoformat(),
        "message": "Tournament created successfully"
    }

@app.get("/admin/tournaments/{tournament_id}/status")
def admin_get_tournament_status(
    tournament_id: int,
    _: bool = Depends(verify_admin)
):
    """Get detailed tournament status (ADMIN ONLY)"""
    
    tournament_data = redis_client.hgetall(f"tournament:{tournament_id}")
    
    if not tournament_data:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    # Get top 10 scores
    top_scores = redis_client.zrevrange(
        f"tournament:{tournament_id}:leaderboard",
        0, 9,
        withscores=True
    )
    
    formatted_scores = []
    for wallet, score in top_scores:
        score_meta = redis_client.hgetall(f"score:{tournament_id}:{wallet}")
        formatted_scores.append({
            "wallet_raw": wallet,
            "score": score,
            "submitted_at": datetime.fromtimestamp(int(score_meta.get("submitted_at", 0))).isoformat() if score_meta else None,
            "is_validated": score_meta.get("is_validated", "false") == "true" if score_meta else False
        })
    
    return {
        "tournament": format_tournament_data(tournament_data, tournament_id),
        "top_scores": formatted_scores
    }

@app.post("/admin/tournaments/{tournament_id}/finalize")
def admin_finalize_tournament(
    tournament_id: int,
    _: bool = Depends(verify_admin)
):
    """Finalize tournament and generate signed payouts (ADMIN ONLY)"""
    
    tournament_data = redis_client.hgetall(f"tournament:{tournament_id}")
    
    if not tournament_data:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    if tournament_data.get("is_finalized") == "true":
        raise HTTPException(status_code=400, detail="Tournament already finalized")
    
    # Get top 3 scores
    top_scores = redis_client.zrevrange(
        f"tournament:{tournament_id}:leaderboard",
        0, 2,
        withscores=True
    )
    
    if len(top_scores) == 0:
        raise HTTPException(status_code=400, detail="No valid scores submitted")
    
    total_pool = float(tournament_data.get("total_pool", 0))
    
    # Calculate prize distribution
    if len(top_scores) == 1:
        prizes = {0: total_pool}
    elif len(top_scores) == 2:
        prizes = {0: total_pool * 0.70, 1: total_pool * 0.30}
    else:
        prizes = {0: total_pool * 0.50, 1: total_pool * 0.30, 2: total_pool * 0.20}
    
    payouts = []
    
    for rank, (wallet, score) in enumerate(top_scores):
        prize_amount = prizes.get(rank, 0)
        
        if prize_amount <= 0:
            continue
        
        nonce = secrets.randbits(64)
        
        payload_hash = hashlib.sha256(
            f"{wallet}{prize_amount}{nonce}{tournament_id}".encode()
        ).hexdigest()
        
        payouts.append({
            "rank": rank + 1,
            "wallet": wallet,
            "score": score,
            "prize_ton": prize_amount,
            "payload_hash": payload_hash,
            "nonce": nonce
        })
        
        # Update user stats
        redis_client.hincrbyfloat(f"user:{wallet}", "total_earned", prize_amount)
        if rank == 0:  # Winner
            redis_client.hincrby(f"user:{wallet}", "tournaments_won", 1)
    
    # Update tournament status
    redis_client.hset(f"tournament:{tournament_id}", mapping={
        "status": "completed",
        "is_finalized": "true"
    })
    
    # Move to completed index
    redis_client.srem("tournaments:active", tournament_id)
    redis_client.srem("tournaments:finalizing", tournament_id)
    redis_client.sadd("tournaments:completed", tournament_id)
    
    return {
        "message": "Tournament finalized",
        "tournament_id": tournament_id,
        "total_pool": total_pool,
        "payouts": payouts
    }

@app.post("/admin/tournaments/{tournament_id}/delete")
def admin_delete_tournament(
    tournament_id: int,
    _: bool = Depends(verify_admin)
):
    """Delete/cancel a tournament (ADMIN ONLY)"""
    
    tournament_data = redis_client.hgetall(f"tournament:{tournament_id}")
    
    if not tournament_data:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    # Delete tournament data
    redis_client.delete(f"tournament:{tournament_id}")
    redis_client.delete(f"tournament:{tournament_id}:leaderboard")
    redis_client.delete(f"tournament:{tournament_id}:entries")
    redis_client.delete(f"tournament:{tournament_id}:participants")
    
    # Delete all score metadata
    participants = redis_client.smembers(f"tournament:{tournament_id}:participants")
    for wallet in participants:
        redis_client.delete(f"score:{tournament_id}:{wallet}")
    
    # Remove from indexes
    redis_client.srem("tournaments:all", tournament_id)
    redis_client.srem("tournaments:active", tournament_id)
    redis_client.srem("tournaments:upcoming", tournament_id)
    redis_client.srem("tournaments:completed", tournament_id)
    redis_client.srem("tournaments:finalizing", tournament_id)
    
    game_type = tournament_data.get("game_type")
    if game_type:
        redis_client.srem(f"tournament:by_game:{game_type}", tournament_id)
    
    return {
        "message": "Tournament deleted",
        "tournament_id": tournament_id
    }

# ===== PUBLIC ENDPOINTS =====

@app.get("/tournaments")
def get_tournaments(
    status: Optional[str] = None,
    game_type: Optional[str] = None
):
    """Get all tournaments (PUBLIC)"""
    
    # Get tournament IDs based on filters
    if status:
        tournament_ids = redis_client.smembers(f"tournaments:{status}")
    else:
        tournament_ids = redis_client.smembers("tournaments:all")
    
    if game_type and tournament_ids:
        game_tournaments = redis_client.smembers(f"tournament:by_game:{game_type}")
        tournament_ids = tournament_ids.intersection(game_tournaments)
    
    # Fetch all tournament data using pipeline (efficient batch operation)
    if not tournament_ids:
        return {"tournaments": []}
    
    pipe = redis_client.pipeline()
    for tid in tournament_ids:
        pipe.hgetall(f"tournament:{tid}")
    
    tournaments_data = pipe.execute()
    
    # Format tournaments
    tournaments = []
    for tid, data in zip(tournament_ids, tournaments_data):
        if data:
            formatted = format_tournament_data(data, tid)
            if formatted:
                tournaments.append(formatted)
    
    # Sort by created_at (most recent first)
    tournaments.sort(key=lambda x: x.get("start_time", ""), reverse=True)
    
    return {"tournaments": tournaments}

@app.get("/tournaments/{tournament_id}")
def get_tournament_details(tournament_id: int):
    """Get tournament details with leaderboard (PUBLIC)"""
    
    tournament_data = redis_client.hgetall(f"tournament:{tournament_id}")
    
    if not tournament_data:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    # Get top 20 scores
    scores = redis_client.zrevrange(
        f"tournament:{tournament_id}:leaderboard",
        0, 19,
        withscores=True
    )
    
    leaderboard = []
    for rank, (wallet, score) in enumerate(scores, 1):
        score_meta = redis_client.hgetall(f"score:{tournament_id}:{wallet}")
        
        leaderboard.append({
            "rank": rank,
            "wallet": wallet[:6] + "..." + wallet[-4:],
            "wallet_full": wallet,
            "score": score,
            "submitted_at": datetime.fromtimestamp(int(score_meta.get("submitted_at", 0))).isoformat() if score_meta else None
        })
    
    return {
        "tournament": format_tournament_data(tournament_data, tournament_id),
        "leaderboard": leaderboard
    }

@app.post("/tournaments/{tournament_id}/submit")
def submit_tournament_score(
    tournament_id: int,
    submission: ScoreSubmission
):
    """Submit a score to a tournament (PUBLIC)"""
    

    
    # Check active tournaments
    active_ids = redis_client.smembers("tournaments:active")
    for tid in active_ids:
        tournament_data = redis_client.hgetall(f"tournament:{tid}")
        end_time = int(tournament_data.get("end_time", 0))
        
        if now >= end_time:
            # End tournament
            redis_client.hset(f"tournament:{tid}", "status", "finalizing")
            redis_client.srem("tournaments:active", tid)
            redis_client.sadd("tournaments:finalizing", tid)
            updated_count += 1
    
    return {
        "message": f"Updated {updated_count} tournaments",
        "timestamp": datetime.now().isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
'''
