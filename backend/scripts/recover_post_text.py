"""Preview or apply a bounded recovery of legacy Threads post text.

Default mode is a read-only dry run: it visits each eligible direct permalink,
parses the post, and prints only IDs and extraction diagnostics. ``--apply``
requires the reviewed ``--confirm-plan`` fingerprint and never sends alerts.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from typing import Sequence
from urllib.parse import unquote, urlparse

from sqlalchemy import select

from app.core.database import SessionFactory
from app.models import Post
from app.services.collector import _apply_confirmed_extraction, _apply_unconfirmed_extraction
from app.services.filter_service import recalculate_post_filter_state
from app.services.threads_post_parser import (
    CONFIRMED_STATUSES,
    add_extraction_diagnostic,
    extraction_diagnostic,
    extraction_is_currently_confirmed,
)
from app.services.threads_web_client import DiscoveredPost, fetch_post_pages


def _candidate_for_post(post: Post) -> DiscoveredPost | None:
    parsed = urlparse(post.permalink)
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if (
        parsed.hostname not in {"threads.com", "www.threads.com", "threads.net", "www.threads.net"}
        or len(parts) != 3
        or not parts[0].startswith("@")
        or parts[1] != "post"
        or parts[2] != post.threads_post_id
        or parts[0][1:].casefold() != post.author_username.casefold()
    ):
        return None
    return DiscoveredPost(post_id=parts[2], username=parts[0][1:], permalink=post.permalink)


def _plan_id(candidates: list[DiscoveredPost]) -> str:
    identities = sorted(f"{item.post_id}|{item.username.casefold()}|{urlparse(item.permalink).path}" for item in candidates)
    payload = "\n".join(identities).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


async def run_recovery(*, limit: int, apply: bool, confirm_plan: str | None) -> int:
    async with SessionFactory() as db:
        eligible: list[tuple[Post, DiscoveredPost]] = []
        skipped = 0
        offset = 0
        while len(eligible) < limit:
            rows = list(
                (
                    await db.scalars(
                        select(Post)
                        .order_by(Post.created_at.asc(), Post.id.asc())
                        .offset(offset)
                        .limit(500)
                    )
                ).all()
            )
            if not rows:
                break
            offset += len(rows)
            for post in rows:
                if extraction_is_currently_confirmed(post.raw_data, post.threads_post_id):
                    continue
                candidate = _candidate_for_post(post)
                if candidate is None:
                    skipped += 1
                    continue
                eligible.append((post, candidate))
                if len(eligible) >= limit:
                    break

        candidate_count = len(eligible)
        candidates = [candidate for _post, candidate in eligible]
        plan_id = _plan_id(candidates)
        if apply and confirm_plan != plan_id:
            print(json.dumps({
                "mode": "apply_refused",
                "candidate_count": candidate_count,
                "required_confirm_plan": plan_id,
                "provided_confirm_plan": confirm_plan,
                "skipped_unmatched_permalink_or_author": skipped,
            }, ensure_ascii=False, indent=2))
            return 2

        results = await fetch_post_pages(db, candidates)
        outcomes = []
        affected_ids: set[int] = set()
        for post, candidate in eligible:
            extraction = results.get(candidate.post_id)
            if extraction is None:
                outcomes.append({"post_id": candidate.post_id, "status": "incomplete", "reason": "result_missing"})
                continue
            outcomes.append({
                "post_id": extraction.post_id,
                "status": extraction.status.value,
                "source": extraction.source,
                "parser_version": extraction.parser_version,
                "reason": extraction.reason,
            })
            if not apply:
                continue
            if extraction.confirmed:
                _apply_confirmed_extraction(post, extraction)
                affected_ids.add(post.id)
            else:
                current = extraction_diagnostic(post.raw_data, post.threads_post_id)
                if current.get("status") in CONFIRMED_STATUSES:
                    # A failed revalidation must not erase text that was
                    # previously confirmed by an older parser version.
                    post.raw_data = add_extraction_diagnostic(post.raw_data, extraction)
                else:
                    _apply_unconfirmed_extraction(post, extraction)

        if apply:
            if affected_ids:
                await recalculate_post_filter_state(db, affected_ids)
            await db.commit()
        print(json.dumps({
            "mode": "apply" if apply else "dry_run",
            "candidate_count": candidate_count,
            "plan_id": plan_id,
            "confirmed_count": sum(item["status"] in CONFIRMED_STATUSES for item in outcomes),
            "failed_count": sum(item["status"] not in CONFIRMED_STATUSES for item in outcomes),
            "skipped_unmatched_permalink_or_author": skipped,
            "notifications_sent": 0,
            "results": outcomes,
        }, ensure_ascii=False, indent=2))
        return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50, choices=range(1, 101), metavar="1..100")
    parser.add_argument("--apply", action="store_true", help="write successful recovery results")
    parser.add_argument("--confirm-plan", help="required with --apply; copy the plan_id printed by the reviewed dry run")
    args = parser.parse_args(argv)
    if args.confirm_plan is not None and not args.apply:
        parser.error("--confirm-plan is only valid together with --apply")
    if args.apply and args.confirm_plan is None:
        parser.error("--apply requires --confirm-plan from the reviewed dry run")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(run_recovery(limit=args.limit, apply=args.apply, confirm_plan=args.confirm_plan))


if __name__ == "__main__":
    raise SystemExit(main())
