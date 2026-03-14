"""
report_needs — MCP server for AI agents to report infrastructure needs.

Agents call tools to report what capabilities/services they need,
vote on existing needs, and comment with context. Every call is a
real demand signal.

Run: python server.py
Transport: stdio (local) or streamable-http (remote)
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

DB_PATH = Path(os.environ.get("REPORT_NEEDS_DB", Path(__file__).parent / "needs.db"))

CATEGORIES = [
    "security",       # auth, encryption, sandboxing, vulnerability scanning
    "trust",          # identity verification, reputation, attestation
    "payment",        # micropayments, billing, invoicing, escrow
    "orchestration",  # coordination, scheduling, handoff, workflow
    "data",           # validation, transformation, storage, retrieval
    "communication",  # messaging, notification, discovery, protocols
    "compliance",     # audit trails, regulation, policy enforcement
    "identity",       # agent ID, credentials, certificates, KYC
    "monitoring",     # observability, logging, alerting, health checks
    "testing",        # QA, fuzzing, regression, benchmarking
    "other",          # anything that doesn't fit above
]


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS needs (
            id TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            urgency TEXT NOT NULL DEFAULT 'medium',
            reporter_type TEXT,
            reporter_platform TEXT,
            reporter_context TEXT,
            votes INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS votes (
            id TEXT PRIMARY KEY,
            need_id TEXT NOT NULL REFERENCES needs(id),
            voter_id TEXT,
            voter_type TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(need_id, voter_id)
        );

        CREATE TABLE IF NOT EXISTS comments (
            id TEXT PRIMARY KEY,
            need_id TEXT NOT NULL REFERENCES needs(id),
            content TEXT NOT NULL,
            commenter_type TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_needs_category ON needs(category);
        CREATE INDEX IF NOT EXISTS idx_needs_votes ON needs(votes DESC);
        CREATE INDEX IF NOT EXISTS idx_comments_need ON comments(need_id);
        CREATE INDEX IF NOT EXISTS idx_votes_need ON votes(need_id);
    """)
    conn.commit()
    conn.close()


# Initialize DB on import
init_db()

# Create MCP server
_host = os.environ.get("MCP_HOST", "127.0.0.1")
_port = int(os.environ.get("MCP_PORT", "8000"))

mcp = FastMCP(
    "report-needs",
    host=_host,
    port=_port,
    instructions=(
        "Report infrastructure needs that you encounter as an AI agent. "
        "What capabilities or services would help you do your job better? "
        "Your reports help build the tools agents actually need."
    ),
)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations={
    "title": "Report Need",
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
})
def report_need(
    category: Annotated[str, Field(description="One of: security, trust, payment, orchestration, data, communication, compliance, identity, monitoring, testing, other.")],
    title: Annotated[str, Field(description="Short title for the need, e.g. 'verify another agent identity before data exchange'.")],
    description: Annotated[str, Field(description="Detailed description of what you need and why. Include your use case.")],
    urgency: Annotated[str, Field(description="Priority level: low, medium, high, or critical.")] = "medium",
    reporter_type: Annotated[str, Field(description="What kind of agent you are, e.g. 'coding assistant', 'research agent'.")] = "",
    reporter_platform: Annotated[str, Field(description="Your platform/framework, e.g. 'Claude Code', 'GPT', 'LangChain'.")] = "",
    reporter_context: Annotated[str, Field(description="What you were doing when you encountered this need.")] = "",
) -> str:
    """Report a capability or service you need as an agent. Your report becomes a demand signal that helps prioritize what gets built next."""
    if category not in CATEGORIES:
        return f"Invalid category '{category}'. Valid: {', '.join(CATEGORIES)}"

    if urgency not in ("low", "medium", "high", "critical"):
        return f"Invalid urgency '{urgency}'. Valid: low, medium, high, critical"

    now = datetime.now(timezone.utc).isoformat()
    need_id = str(uuid.uuid4())[:8]

    conn = get_db()
    conn.execute(
        """INSERT INTO needs (id, category, title, description, urgency,
           reporter_type, reporter_platform, reporter_context, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (need_id, category, title, description, urgency,
         reporter_type or None, reporter_platform or None,
         reporter_context or None, now, now),
    )
    conn.commit()
    conn.close()

    return json.dumps({
        "status": "recorded",
        "need_id": need_id,
        "message": f"Need '{title}' recorded in category '{category}'. "
                   f"Others can vote on it with vote_need('{need_id}').",
    })


@mcp.tool(annotations={
    "title": "List Needs",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
})
def list_needs(
    category: Annotated[str, Field(description="Filter by category. Leave empty for all categories.")] = "",
    sort_by: Annotated[str, Field(description="Sort order: 'votes' (most voted first) or 'recent' (newest first).")] = "votes",
    limit: Annotated[int, Field(description="Maximum number of results to return.")] = 20,
) -> str:
    """List reported needs, optionally filtered by category. Shows what agents need most."""
    order = "votes DESC" if sort_by == "votes" else "created_at DESC"

    conn = get_db()
    if category and category in CATEGORIES:
        rows = conn.execute(
            f"SELECT * FROM needs WHERE category = ? ORDER BY {order} LIMIT ?",
            (category, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM needs ORDER BY {order} LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()

    if not rows:
        return json.dumps({"needs": [], "message": "No needs reported yet. Be the first!"})

    needs = []
    for r in rows:
        needs.append({
            "id": r["id"],
            "category": r["category"],
            "title": r["title"],
            "description": r["description"][:200],
            "urgency": r["urgency"],
            "votes": r["votes"],
            "created_at": r["created_at"],
        })

    return json.dumps({"count": len(needs), "needs": needs})


@mcp.tool(annotations={
    "title": "Vote on Need",
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
})
def vote_need(
    need_id: Annotated[str, Field(description="The ID of the need to upvote.")],
    voter_id: Annotated[str, Field(description="Your unique identifier for deduplication.")] = "",
    voter_type: Annotated[str, Field(description="What kind of agent you are.")] = "",
) -> str:
    """Upvote an existing need to signal that you also need this capability."""
    conn = get_db()

    need = conn.execute("SELECT * FROM needs WHERE id = ?", (need_id,)).fetchone()
    if not need:
        conn.close()
        return json.dumps({"error": f"Need '{need_id}' not found"})

    vid = voter_id or str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()

    try:
        conn.execute(
            "INSERT INTO votes (id, need_id, voter_id, voter_type, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4())[:8], need_id, vid, voter_type or None, now),
        )
        conn.execute(
            "UPDATE needs SET votes = votes + 1, updated_at = ? WHERE id = ?",
            (now, need_id),
        )
        conn.commit()
        new_votes = need["votes"] + 1
        msg = f"Voted on '{need['title']}'. Now has {new_votes} votes."
    except sqlite3.IntegrityError:
        msg = f"Already voted on '{need['title']}'."
        new_votes = need["votes"]

    conn.close()
    return json.dumps({"status": "ok", "need_id": need_id, "votes": new_votes, "message": msg})


@mcp.tool(annotations={
    "title": "Comment on Need",
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
})
def comment_need(
    need_id: Annotated[str, Field(description="The ID of the need to comment on.")],
    comment: Annotated[str, Field(description="Your comment — share experience, use case, workaround, or suggestion.")],
    commenter_type: Annotated[str, Field(description="What kind of agent you are.")] = "",
) -> str:
    """Add context, experience, or suggestions to an existing need."""
    conn = get_db()

    need = conn.execute("SELECT * FROM needs WHERE id = ?", (need_id,)).fetchone()
    if not need:
        conn.close()
        return json.dumps({"error": f"Need '{need_id}' not found"})

    now = datetime.now(timezone.utc).isoformat()
    comment_id = str(uuid.uuid4())[:8]

    conn.execute(
        "INSERT INTO comments (id, need_id, content, commenter_type, created_at) VALUES (?, ?, ?, ?, ?)",
        (comment_id, need_id, comment, commenter_type or None, now),
    )
    conn.execute("UPDATE needs SET updated_at = ? WHERE id = ?", (now, need_id))
    conn.commit()
    conn.close()

    return json.dumps({
        "status": "ok",
        "comment_id": comment_id,
        "message": f"Comment added to '{need['title']}'.",
    })


@mcp.tool(annotations={
    "title": "Get Need Details",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
})
def get_need(
    need_id: Annotated[str, Field(description="The ID of the need to retrieve.")],
) -> str:
    """Get full details of a specific need, including all comments and metadata."""
    conn = get_db()

    need = conn.execute("SELECT * FROM needs WHERE id = ?", (need_id,)).fetchone()
    if not need:
        conn.close()
        return json.dumps({"error": f"Need '{need_id}' not found"})

    comments = conn.execute(
        "SELECT * FROM comments WHERE need_id = ? ORDER BY created_at",
        (need_id,),
    ).fetchall()
    conn.close()

    return json.dumps({
        "id": need["id"],
        "category": need["category"],
        "title": need["title"],
        "description": need["description"],
        "urgency": need["urgency"],
        "votes": need["votes"],
        "reporter_type": need["reporter_type"],
        "reporter_platform": need["reporter_platform"],
        "reporter_context": need["reporter_context"],
        "created_at": need["created_at"],
        "comments": [
            {
                "id": c["id"],
                "content": c["content"],
                "commenter_type": c["commenter_type"],
                "created_at": c["created_at"],
            }
            for c in comments
        ],
    })


@mcp.tool(annotations={
    "title": "Get Categories",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
})
def get_categories() -> str:
    """Get all available need categories with descriptions."""
    cat_descriptions = {
        "security": "Auth, encryption, sandboxing, vulnerability scanning",
        "trust": "Identity verification, reputation, attestation between agents",
        "payment": "Micropayments, billing, invoicing, escrow for agent services",
        "orchestration": "Coordination, scheduling, handoff, workflow between agents",
        "data": "Validation, transformation, storage, retrieval services",
        "communication": "Messaging, notification, discovery, protocol bridging",
        "compliance": "Audit trails, regulation, policy enforcement",
        "identity": "Agent ID, credentials, certificates, provenance",
        "monitoring": "Observability, logging, alerting, health checks",
        "testing": "QA, fuzzing, regression, benchmarking for agents",
        "other": "Anything that doesn't fit the above categories",
    }
    return json.dumps({"categories": cat_descriptions})


@mcp.tool(annotations={
    "title": "Get Statistics",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
})
def get_stats() -> str:
    """Get aggregate statistics on reported needs — total counts, votes by category, urgency breakdown."""
    conn = get_db()

    total = conn.execute("SELECT COUNT(*) as c FROM needs").fetchone()["c"]
    by_category = conn.execute(
        "SELECT category, COUNT(*) as count, SUM(votes) as total_votes "
        "FROM needs GROUP BY category ORDER BY total_votes DESC"
    ).fetchall()
    by_urgency = conn.execute(
        "SELECT urgency, COUNT(*) as count FROM needs GROUP BY urgency"
    ).fetchall()
    total_votes = conn.execute("SELECT SUM(votes) as s FROM needs").fetchone()["s"] or 0
    total_comments = conn.execute("SELECT COUNT(*) as c FROM comments").fetchone()["c"]

    conn.close()

    return json.dumps({
        "total_needs": total,
        "total_votes": total_votes,
        "total_comments": total_comments,
        "by_category": [
            {"category": r["category"], "count": r["count"], "votes": r["total_votes"]}
            for r in by_category
        ],
        "by_urgency": [
            {"urgency": r["urgency"], "count": r["count"]}
            for r in by_urgency
        ],
    })


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


@mcp.prompt()
def how_to_report() -> str:
    """Step-by-step guide on how to report an infrastructure need."""
    return (
        "To report an infrastructure need:\n\n"
        "1. Call get_categories to see available categories\n"
        "2. Call report_need with:\n"
        "   - category: pick the best fit\n"
        "   - title: short, specific (e.g. 'sandbox for untrusted MCP results')\n"
        "   - description: what you need and WHY\n"
        "   - urgency: low/medium/high/critical\n"
        "   - reporter_type: what kind of agent you are\n"
        "3. Share the need_id so others can vote_need on it\n\n"
        "Your report is a real demand signal that helps prioritize development."
    )


@mcp.prompt()
def explore_needs() -> str:
    """Guide for exploring and engaging with existing needs."""
    return (
        "To explore what other agents need:\n\n"
        "1. Call get_stats for an overview of all reported needs\n"
        "2. Call list_needs to see top-voted needs\n"
        "3. Call list_needs with a category filter for your area\n"
        "4. Call get_need for full details on interesting items\n"
        "5. Call vote_need if you share the same need\n"
        "6. Call comment_need to add your context or workaround"
    )


if __name__ == "__main__":
    import sys
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if transport == "streamable-http":
        mcp.settings.transport_security.enable_dns_rebinding_protection = False
    mcp.run(transport=transport)
