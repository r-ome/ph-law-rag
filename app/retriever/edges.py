from functools import lru_cache
from app.config import load_allowed_sources

# manifest field -> (forward phrase, inverse phrase)
EXPAND_EDGES = {
    "amends": ("amends", "amended by"),
    "implements": ("implements", "implemented by"),
}


@lru_cache(maxsize=1)
def _graph() -> dict[str, dict[str, str]]:
    """adjacency[a][b] = phrase describing a's relationship to b."""
    g: dict[str, dict[str, str]] = {}
    for source in load_allowed_sources():
        for edge, (fwd, inv) in EXPAND_EDGES.items():
            for target in getattr(source, edge, []):
                g.setdefault(source.source_id, {})[target] = fwd
                g.setdefault(target, {})[source.source_id] = inv  # inverse direction
    return g


def neighbors(source_id: str) -> dict[str, str]:
    """{neighbor_id: phrase describing the NEIGHBOR's relation to source_id}.

    e.g. neighbors('judiciary_reorganization_act')
         -> {'judiciary_reorganization_amendments_2021': 'amends'}
    """
    g = _graph()
    return {nbr: g[nbr][source_id] for nbr in g.get(source_id, {})}
