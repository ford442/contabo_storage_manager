"""Leaderboard endpoints for high scores."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .config import settings

logger = logging.getLogger(__name__)
leaderboard_router = APIRouter(prefix="/api", tags=["leaderboard"])


# ====================== Models ======================

class ScoreEntry(BaseModel):
    name: str = Field(..., min_length=1, max_length=10, description="Player name (3-10 chars)")
    score: int = Field(..., ge=0, description="Score value")
    map_id: str = Field(default="neon-helix", description="Map/level ID")
    adventure_level: Optional[str] = Field(default=None, description="Adventure mode level")
    balls: int = Field(default=1, ge=1, description="Number of balls used")
    combo_max: int = Field(default=0, ge=0, description="Max combo achieved")


class LeaderboardEntry(BaseModel):
    rank: int
    name: str
    score: int
    map_id: str
    adventure_level: Optional[str]
    balls: int
    combo_max: int
    date: str


class LeaderboardResponse(BaseModel):
    scores: List[LeaderboardEntry]
    total: int
    map_id: Optional[str] = None
    adventure_level: Optional[str] = None


class SubmitScoreResponse(BaseModel):
    success: bool
    rank: Optional[int] = None
    message: str


# ====================== Helpers ======================

def _get_leaderboard_dir() -> Path:
    """Get the leaderboard storage directory."""
    base = Path(settings.files_dir)
    leaderboard_dir = base / "leaderboard"
    leaderboard_dir.mkdir(parents=True, exist_ok=True)
    return leaderboard_dir


def _get_index_file() -> Path:
    """Get the main leaderboard index file."""
    return _get_leaderboard_dir() / "index.json"


def _load_leaderboard() -> List[dict]:
    """Load all scores from the leaderboard index."""
    index_file = _get_index_file()
    
    if not index_file.exists():
        return []
    
    try:
        with open(index_file, "r") as f:
            data = json.load(f)
            return data.get("scores", [])
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Failed to load leaderboard: {e}")
        return []


def _save_leaderboard(scores: List[dict]) -> None:
    """Save scores to the leaderboard index."""
    index_file = _get_index_file()
    
    with open(index_file, "w") as f:
        json.dump({
            "scores": scores,
            "updated": datetime.now(timezone.utc).isoformat(),
            "count": len(scores)
        }, f, indent=2)


def _filter_scores(
    scores: List[dict],
    map_id: Optional[str] = None,
    adventure_level: Optional[str] = None,
    limit: int = 10
) -> List[dict]:
    """Filter and sort scores."""
    filtered = scores
    
    if map_id:
        filtered = [s for s in filtered if s.get("map_id") == map_id]
    
    if adventure_level:
        filtered = [s for s in filtered if s.get("adventure_level") == adventure_level]
    
    # Sort by score descending, then by date ascending (earlier = better)
    filtered.sort(key=lambda x: (-x.get("score", 0), x.get("date", "")))
    
    return filtered[:limit]


def _calculate_rank(scores: List[dict], new_score: int, map_id: str, adventure_level: Optional[str]) -> int:
    """Calculate the rank of a new score."""
    filtered = _filter_scores(scores, map_id, adventure_level, limit=1000)
    
    for i, score in enumerate(filtered, 1):
        if new_score > score.get("score", 0):
            return i
    
    return len(filtered) + 1


# ====================== Endpoints ======================

@leaderboard_router.get("/leaderboard", response_model=LeaderboardResponse)
async def get_leaderboard(
    map_id: Optional[str] = Query(None, description="Filter by map ID"),
    adventure_level: Optional[str] = Query(None, description="Filter by adventure level"),
    limit: int = Query(10, ge=1, le=100, description="Number of scores to return")
):
    """Get the top scores, optionally filtered by map and adventure level."""
    scores = _load_leaderboard()
    filtered = _filter_scores(scores, map_id, adventure_level, limit)
    
    # Add rank to each entry
    ranked = []
    for i, score in enumerate(filtered, 1):
        entry = {
            "rank": i,
            "name": score.get("name", "???"),
            "score": score.get("score", 0),
            "map_id": score.get("map_id", "unknown"),
            "adventure_level": score.get("adventure_level"),
            "balls": score.get("balls", 1),
            "combo_max": score.get("combo_max", 0),
            "date": score.get("date", datetime.now(timezone.utc).isoformat())
        }
        ranked.append(entry)
    
    return LeaderboardResponse(
        scores=ranked,
        total=len(scores),
        map_id=map_id,
        adventure_level=adventure_level
    )


@leaderboard_router.post("/leaderboard", response_model=SubmitScoreResponse)
async def submit_score(entry: ScoreEntry):
    """Submit a new score to the leaderboard."""
    try:
        scores = _load_leaderboard()
        
        # Calculate rank before adding
        rank = _calculate_rank(scores, entry.score, entry.map_id, entry.adventure_level)
        
        # Create score entry
        score_data = {
            "name": entry.name.upper()[:10],  # Uppercase and truncate
            "score": entry.score,
            "map_id": entry.map_id,
            "adventure_level": entry.adventure_level,
            "balls": entry.balls,
            "combo_max": entry.combo_max,
            "date": datetime.now(timezone.utc).isoformat()
        }
        
        # Add to scores
        scores.append(score_data)
        
        # Keep only top 1000 scores per filter combination (pruning)
        # For now, just keep all scores (GCS will handle storage)
        
        _save_leaderboard(scores)
        
        logger.info(f"Score submitted: {entry.name} - {entry.score} (rank #{rank})")
        
        return SubmitScoreResponse(
            success=True,
            rank=rank,
            message=f"Score submitted! You ranked #{rank}"
        )
        
    except Exception as e:
        logger.error(f"Failed to submit score: {e}")
        raise HTTPException(status_code=500, detail="Failed to save score")


@leaderboard_router.get("/leaderboard/player-rank")
async def get_player_rank(
    score: int = Query(..., ge=0, description="Player score"),
    map_id: str = Query(..., description="Map ID"),
    adventure_level: Optional[str] = Query(None, description="Adventure level")
):
    """Get the rank for a specific score without submitting it."""
    scores = _load_leaderboard()
    rank = _calculate_rank(scores, score, map_id, adventure_level)
    
    return {
        "rank": rank,
        "score": score,
        "map_id": map_id,
        "adventure_level": adventure_level
    }


@leaderboard_router.get("/leaderboard/maps")
async def get_leaderboard_maps():
    """Get all map IDs that have leaderboard entries."""
    scores = _load_leaderboard()
    
    maps = {}
    for score in scores:
        map_id = score.get("map_id", "unknown")
        if map_id not in maps:
            maps[map_id] = {"count": 0, "top_score": 0}
        maps[map_id]["count"] += 1
        maps[map_id]["top_score"] = max(maps[map_id]["top_score"], score.get("score", 0))
    
    return {"maps": maps}
