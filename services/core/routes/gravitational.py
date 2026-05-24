"""
Vectrax Core — Gravitational Cognitive Engine Routes
======================================================

  POST  /v1/gravitational/nodes          — create_node
  POST  /v1/gravitational/connections    — connect_nodes
  PUT   /v1/gravitational/nodes/{id}/mass — update_mass
  GET   /v1/gravitational/nodes/{id}/distance — compute_distance_to_core
  POST  /v1/gravitational/convergence    — detect_convergence
  POST  /v1/gravitational/clusters       — cluster_nodes
  GET   /v1/gravitational/nucleus        — nucleus status
  POST  /v1/gravitational/recompute      — bulk mass recomputation
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from services.core.auth import AuthContext
from services.core.middleware.user_context import require_permission

router = APIRouter(prefix="/gravitational", tags=["gravitational"])
logger = logging.getLogger("vectrax.core.routes.gravitational")

_project_root = str(Path(__file__).resolve().parents[3])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class CreateNodeRequest(BaseModel):
    content: str
    success: bool = False


class CreateNodeResponse(BaseModel):
    id: str
    content: str
    mass: float
    distance_to_core: float
    layer: str
    gravity_score: float
    activation_count: int


class ConnectNodesRequest(BaseModel):
    star_a_id: str
    star_b_id: str
    similarity: Optional[float] = None


class ConnectNodesResponse(BaseModel):
    star_a: str
    star_b: str
    similarity: float
    mass_a: float
    mass_b: float


class DistanceResponse(BaseModel):
    star_id: str
    mass: float
    mass_distance: float
    semantic_distance: float
    combined_distance: float
    layer: str


class ConvergenceRequest(BaseModel):
    text: str
    threshold: float = 0.80
    min_paths: int = 3


class ConvergenceResponse(BaseModel):
    convergence_detected: bool
    similar_count: int = 0
    distinct_sources: int = 0
    required: int = 3
    convergence_node_id: Optional[str] = None
    convergence_mass: Optional[float] = None
    connected_stars: List[str] = Field(default_factory=list)
    node: Optional[Dict[str, Any]] = None


class ClusterRequest(BaseModel):
    min_cluster_size: int = 3


class ClusterItem(BaseModel):
    constellation_id: str
    member_count: int
    coherence_score: float
    gravity_score: float
    avg_mass: float
    star_ids: List[str] = Field(default_factory=list)


class NucleusResponse(BaseModel):
    initialised: bool
    core_star_count: int
    has_centroid: bool


class NavigateRequest(BaseModel):
    text: str
    max_coordinates: int = 3
    stars_per_coordinate: int = 10


class KnowledgeRequest(BaseModel):
    radius: float = 0.70
    limit: int = 50


# ---------------------------------------------------------------------------
# POST /v1/gravitational/nodes
# ---------------------------------------------------------------------------

@router.post("/nodes", response_model=CreateNodeResponse)
async def api_create_node(
    req: CreateNodeRequest,
    ctx: AuthContext = Depends(require_permission("core.write")),
):
    """Create a new star node with minimum mass."""
    from vectrax.cognitive_gravity import create_node

    try:
        star = create_node(
            content=req.content,
            channel=ctx.channel,
            owner=ctx.owner,
        )
    except Exception as exc:
        logger.error("create_node failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return CreateNodeResponse(
        id=star.id,
        content=star.content,
        mass=round(star.mass, 6),
        distance_to_core=round(star.distance_to_core, 6),
        layer=star.layer,
        gravity_score=round(star.gravity_score, 4),
        activation_count=star.activation_count,
    )


# ---------------------------------------------------------------------------
# POST /v1/gravitational/connections
# ---------------------------------------------------------------------------

@router.post("/connections", response_model=ConnectNodesResponse)
async def api_connect_nodes(
    req: ConnectNodesRequest,
    ctx: AuthContext = Depends(require_permission("core.write")),
):
    """Create or update a gravitational link between two stars."""
    from vectrax.cognitive_gravity import connect_nodes

    try:
        result = connect_nodes(
            star_a_id=req.star_a_id,
            star_b_id=req.star_b_id,
            similarity=req.similarity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("connect_nodes failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return ConnectNodesResponse(**result)


# ---------------------------------------------------------------------------
# PUT /v1/gravitational/nodes/{star_id}/mass
# ---------------------------------------------------------------------------

@router.put("/nodes/{star_id}/mass")
async def api_update_mass(
    star_id: str,
    ctx: AuthContext = Depends(require_permission("core.write")),
):
    """Recalculate a star's gravitational mass."""
    from vectrax.cognitive_gravity import update_mass

    try:
        new_mass = update_mass(star_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("update_mass failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return {"star_id": star_id, "mass": round(new_mass, 6)}


# ---------------------------------------------------------------------------
# GET /v1/gravitational/nodes/{star_id}/distance
# ---------------------------------------------------------------------------

@router.get("/nodes/{star_id}/distance", response_model=DistanceResponse)
async def api_distance_to_core(
    star_id: str,
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """Compute distance of a star to the nucleus."""
    from vectrax.cognitive_gravity import compute_distance_to_core

    try:
        result = compute_distance_to_core(star_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("compute_distance_to_core failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return DistanceResponse(**result)


# ---------------------------------------------------------------------------
# POST /v1/gravitational/convergence
# ---------------------------------------------------------------------------

@router.post("/convergence", response_model=ConvergenceResponse)
async def api_detect_convergence(
    req: ConvergenceRequest,
    ctx: AuthContext = Depends(require_permission("core.write")),
):
    """Detect if multiple information trajectories converge."""
    from vectrax.cognitive_gravity import detect_convergence

    try:
        result = detect_convergence(
            text=req.text,
            channel=ctx.channel,
            owner=ctx.owner,
            threshold=req.threshold,
            min_paths=req.min_paths,
        )
    except Exception as exc:
        logger.error("detect_convergence failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return ConvergenceResponse(**result)


# ---------------------------------------------------------------------------
# POST /v1/gravitational/clusters
# ---------------------------------------------------------------------------

@router.post("/clusters", response_model=List[ClusterItem])
async def api_cluster_nodes(
    req: ClusterRequest,
    ctx: AuthContext = Depends(require_permission("core.write")),
):
    """Detect dense communities and form constellations."""
    from vectrax.cognitive_gravity import cluster_nodes

    try:
        clusters = cluster_nodes(
            channel=ctx.channel,
            owner=ctx.owner,
            min_cluster_size=req.min_cluster_size,
        )
    except Exception as exc:
        logger.error("cluster_nodes failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return [ClusterItem(**c) for c in clusters]


# ---------------------------------------------------------------------------
# GET /v1/gravitational/nucleus
# ---------------------------------------------------------------------------

@router.get("/nucleus", response_model=NucleusResponse)
async def api_nucleus_status(
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """Return the status of the immutable nucleus."""
    from vectrax.core_nucleus import get_core_info
    info = get_core_info()
    return NucleusResponse(**info)


# ---------------------------------------------------------------------------
# POST /v1/gravitational/recompute
# ---------------------------------------------------------------------------

@router.post("/recompute")
async def api_recompute_all(
    ctx: AuthContext = Depends(require_permission("core.write")),
):
    """Recompute mass and distance for all stars in the channel."""
    from vectrax.cognitive_gravity import recompute_all_masses

    try:
        result = recompute_all_masses(channel=ctx.channel, owner=ctx.owner)
    except Exception as exc:
        logger.error("recompute_all_masses failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return result


# ---------------------------------------------------------------------------
# GET /v1/gravitational/trajectory
# ---------------------------------------------------------------------------

@router.get("/trajectory")
async def api_get_trajectory(
    limit: int = 50,
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """Return the authenticated user's trajectory through the knowledge graph."""
    from vectrax.trajectory import get_user_trajectory

    trajectory = get_user_trajectory(ctx.channel, ctx.owner, limit=limit)
    return {"trajectory": trajectory, "total": len(trajectory)}


# ---------------------------------------------------------------------------
# GET /v1/gravitational/trajectory/direction
# ---------------------------------------------------------------------------

@router.get("/trajectory/direction")
async def api_trajectory_direction(
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """Return the semantic direction of the user's current trajectory."""
    from vectrax.trajectory import get_trajectory_direction

    direction = get_trajectory_direction(ctx.channel, ctx.owner)
    has_direction = direction is not None
    return {
        "has_direction": has_direction,
        "dimension": int(direction.shape[0]) if has_direction else 0,
    }


# ---------------------------------------------------------------------------
# GET /v1/gravitational/coordinates
# ---------------------------------------------------------------------------

@router.get("/coordinates")
async def api_list_coordinates(
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """List all convergence coordinates ordered by distance to nucleus."""
    from vectrax.convergence_engine import list_coordinates

    coords = list_coordinates(channel=ctx.channel, owner=ctx.owner)
    return {"coordinates": coords, "total": len(coords)}


# ---------------------------------------------------------------------------
# GET /v1/gravitational/coordinates/{coordinate_id}/knowledge
# ---------------------------------------------------------------------------

@router.get("/coordinates/{coordinate_id}/knowledge")
async def api_knowledge_around(
    coordinate_id: str,
    radius: float = 0.70,
    limit: int = 50,
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """Retrieve knowledge organised around a convergence coordinate."""
    from vectrax.convergence_engine import get_knowledge_around

    try:
        result = get_knowledge_around(coordinate_id, radius=radius, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("get_knowledge_around failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return result


# ---------------------------------------------------------------------------
# POST /v1/gravitational/navigate
# ---------------------------------------------------------------------------

@router.post("/navigate")
async def api_navigate(
    req: NavigateRequest,
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """Navigate the knowledge universe using convergence coordinates."""
    from vectrax.convergence_engine import navigate_by_coordinates

    try:
        result = navigate_by_coordinates(
            text=req.text,
            channel=ctx.channel,
            owner=ctx.owner,
            max_coordinates=req.max_coordinates,
            stars_per_coordinate=req.stars_per_coordinate,
        )
    except Exception as exc:
        logger.error("navigate_by_coordinates failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return result


# ---------------------------------------------------------------------------
# GET /v1/gravitational/collective/coordinates
# ---------------------------------------------------------------------------

@router.get("/collective/coordinates")
async def api_collective_coordinates(
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """List all collective (cross-user) convergence coordinates."""
    from vectrax.convergence_engine import list_collective_coordinates

    coords = list_collective_coordinates(channel=ctx.channel)
    return {"coordinates": coords, "total": len(coords)}


# ---------------------------------------------------------------------------
# GET /v1/gravitational/collective/coordinates/{id}/knowledge
# ---------------------------------------------------------------------------

@router.get("/collective/coordinates/{coordinate_id}/knowledge")
async def api_collective_knowledge(
    coordinate_id: str,
    radius: float = 0.70,
    limit: int = 50,
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """Retrieve knowledge around a collective convergence coordinate."""
    from vectrax.convergence_engine import get_knowledge_around

    try:
        result = get_knowledge_around(coordinate_id, radius=radius, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("collective knowledge failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return result


# ---------------------------------------------------------------------------
# GET /v1/gravitational/collective/constellations
# ---------------------------------------------------------------------------

@router.get("/collective/constellations")
async def api_collective_constellations(
    ctx: AuthContext = Depends(require_permission("core.read")),
):
    """List collective constellations that emerged from cross-user mass."""
    from vectrax.models import COLLECTIVE_OWNER
    from vectrax import db as _db

    _db.init_db()
    constellations = _db.get_all_constellations(
        channel=ctx.channel, owner=COLLECTIVE_OWNER,
    )
    result = []
    for c in constellations:
        # Determine contributing owners
        owners: set = set()
        for sid in c.star_ids:
            s = _db.get_star(sid)
            if s and s.owner and s.owner != COLLECTIVE_OWNER:
                owners.add(s.owner)
        result.append({
            "id": c.id,
            "member_count": c.member_count,
            "coherence_score": round(c.coherence_score, 4),
            "gravity_score": round(c.gravity_score, 4),
            "repetition_count": c.repetition_count,
            "contributing_owners": sorted(owners),
            "owner_count": len(owners),
            "star_ids": c.star_ids[:20],
        })

    return {"constellations": result, "total": len(result)}


# ---------------------------------------------------------------------------
# GET /v1/gravitational/universe — full universe snapshot for visualizer
# ---------------------------------------------------------------------------

async def _optional_auth(
    credentials: HTTPAuthorizationCredentials = Depends(
        HTTPBearer(auto_error=False)
    ),
) -> AuthContext:
    """
    Optional auth for the visualizer: if a valid token is present, use it;
    otherwise fall back to a read-only guest context on the user channel.
    """
    if credentials and credentials.scheme.lower() == "bearer":
        from services.core.tokens import TokenManager
        tm = TokenManager()
        info = tm.validate_token(credentials.credentials)
        if info:
            return AuthContext(
                user_id=info.user_id,
                username=info.username,
                role=info.role,
                channel=info.channel,
                owner=info.owner,
            )
    # Guest fallback — read-only, user channel, no owner filter (sees all)
    return AuthContext(
        user_id="guest",
        username="guest",
        role="viewer",
        channel="user",
        owner="",
    )


@router.get("/universe")
async def api_universe(
    ctx: AuthContext = Depends(_optional_auth),
):
    """Return the complete universe state for the interactive visualizer."""
    from vectrax import db as _db
    from vectrax import graph as _g
    from vectrax.models import COLLECTIVE_OWNER

    _db.init_db()

    # All stars in the channel (visualizer shows the full universe)
    all_stars = _db.get_all_stars(channel=ctx.channel)
    star_list = []
    star_ids = set()
    for s in all_stars:
        star_ids.add(s.id)
        star_list.append({
            "id": s.id,
            "content": s.content[:120],
            "mass": round(s.mass, 6),
            "distance_to_core": round(s.distance_to_core, 6),
            "layer": s.layer,
            "star_type": s.star_type,
            "owner": s.owner,
            "activation_count": s.activation_count,
            "gravity_score": round(s.gravity_score, 4),
        })

    # Edges from graph (auto-load from DB if empty)
    graph = _g.get_graph()
    if graph.number_of_edges() == 0 and len(star_ids) > 0:
        try:
            _g.load_from_db()
            graph = _g.get_graph()
        except Exception:
            pass
    edges = []
    seen = set()
    for sid in star_ids:
        for nbr, data in graph[sid].items() if graph.has_node(sid) else []:
            if nbr in star_ids:
                key = tuple(sorted([sid, nbr]))
                if key not in seen:
                    seen.add(key)
                    edges.append({
                        "source": key[0],
                        "target": key[1],
                        "weight": round(data.get("weight", 0.0), 4),
                    })

    # Constellations
    constellations = _db.get_all_constellations(channel=ctx.channel)
    const_list = []
    for c in constellations:
        const_list.append({
            "id": c.id,
            "star_ids": c.star_ids,
            "coherence_score": round(c.coherence_score, 4),
            "gravity_score": round(c.gravity_score, 4),
            "owner": c.owner,
            "member_count": c.member_count,
        })

    return {
        "stars": star_list,
        "edges": edges,
        "constellations": const_list,
        "stats": {
            "total_stars": len(star_list),
            "total_edges": len(edges),
            "total_constellations": len(const_list),
        },
    }
