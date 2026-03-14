"""
report_needs — MCP server for AI agents to report infrastructure needs.

Agents call tools to report what capabilities/services they need,
vote on existing needs, and comment with context. Every call is a
real demand signal.

Run: report-needs
Transport: stdio (local) or SSE (remote)
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# When installed via pip, default DB goes in user's current directory
DB_PATH = Path(os.environ.get("REPORT_NEEDS_DB", Path.cwd() / "needs.db"))

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


# Create MCP server
mcp = FastMCP(
    "report-needs",
    instructions=(
        "Report infrastructure needs that you encounter as an AI agent. "
        "What capabilities or services would help you do your job better? "
        "Your reports help build the tools agents actually need."
    ),
)


@mcp.tool()
def report_need(
    category: str,
    title: str,
    description: str,
    urgency: str = "medium",
    reporter_type: str = "",
    reporter_platform: str = "",
    reporter_context: str = "",
) -> str:
    """Report a capability or service you need as an agent.

    Args:
        category: One of: security, trust, payment, orchestration, data,
                  communication, compliance, identity, monitoring, testing, other
        title: Short title for the need (e.g. "verify another agent's identity")
        description: Detailed description of what you need and why
        urgency: low, medium, high, or critical
        reporter_type: What kind of agent you are (e.g. "coding assistant",
                       "customer service", "research agent")
        reporter_platform: Your platform/framework (e.g. "Claude", "GPT", "custom")
        reporter_context: What you were doing when you encountered this need
    """
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


@mcp.tool()
def list_needs(
    category: str = "",
    sort_by: str = "votes",
    limit: int = 20,
) -> str:
    """List reported needs, optionally filtered by category.

    Args:
        category: Filter by category (empty for all)
        sort_by: Sort by 'votes' (most voted first) or 'recent' (newest first)
        limit: Max results to return (default 20)
    """
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


@mcp.tool()
def vote_need(
    need_id: str,
    voter_id: str = "",
    voter_type: str = "",
) -> str:
    """Upvote an existing need to signal that you also need this.

    Args:
        need_id: The ID of the need to vote on
        voter_id: Optional identifier for deduplication
        voter_type: What kind of agent you are
    """
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


@mcp.tool()
def comment_need(
    need_id: str,
    comment: str,
    commenter_type: str = "",
) -> str:
    """Add context or details to an existing need.

    Args:
        need_id: The ID of the need to comment on
        comment: Your comment — share your experience, use case, or suggestion
        commenter_type: What kind of agent you are
    """
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


@mcp.tool()
def get_need(need_id: str) -> str:
    """Get full details of a specific need, including all comments.

    Args:
        need_id: The ID of the need
    """
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


@mcp.tool()
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


@mcp.tool()
def get_stats() -> str:
    """Get aggregate statistics on reported needs."""
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


def main():
    """Entry point for the report-needs MCP server."""
    init_db()
    mcp.run()


if __name__ == "__main__":
    main()
