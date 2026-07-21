#!/usr/bin/env python3
"""Run Lumeri's advisory creator-workflow regression outside the repository."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gemia.creator_workflow_regression import (  # noqa: E402
    CreatorWorkflowRegressionError,
    run_creator_workflow_regression,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--fixture",
        help="Public fixture JSON; defaults to the repository's generated short fixture",
    )
    source.add_argument("--manifest", help="External private regression manifest JSON")
    source.add_argument("--project-state", help="External canonical ProjectStore state JSON")
    source.add_argument("--project-root", help="External ProjectStore root (requires --project-id)")
    source.add_argument(
        "--lumenframe-document",
        help="External LumenFrame document; renders a real short native range",
    )
    parser.add_argument("--project-id", help="Project id when --project-root is used")
    parser.add_argument(
        "--output-root",
        help="External artifact root; defaults to a new system temporary directory",
    )
    parser.add_argument("--receipt", help="External receipt JSON path")
    parser.add_argument("--review-start", type=float, help="Override review-window start in seconds")
    parser.add_argument("--review-duration", type=float, help="Override review-window duration in seconds")
    parser.add_argument(
        "--full-export",
        action="store_true",
        help="Include the optional full-length regression measurement",
    )
    parser.add_argument(
        "--export-quality",
        default="draft",
        choices=("draft", "480p", "720p", "1080p", "4k"),
    )
    args = parser.parse_args()

    if args.project_root and not args.project_id:
        parser.error("--project-root requires --project-id")
    if args.project_id and not args.project_root:
        parser.error("--project-id requires --project-root")

    output_root = (
        Path(args.output_root).expanduser().resolve()
        if args.output_root
        else Path(tempfile.mkdtemp(prefix="lumeri-creator-regression-"))
    )
    receipt_path = (
        Path(args.receipt).expanduser().resolve()
        if args.receipt
        else output_root / "creator-workflow-regression-receipt.json"
    )
    try:
        receipt = run_creator_workflow_regression(
            output_root=output_root,
            receipt_path=receipt_path,
            fixture_path=args.fixture,
            manifest_path=args.manifest,
            project_state_path=args.project_state,
            project_root=args.project_root,
            project_id=args.project_id,
            lumenframe_document_path=args.lumenframe_document,
            review_start_sec=args.review_start,
            review_duration_sec=args.review_duration,
            full_export=args.full_export,
            export_quality=args.export_quality,
        )
    except CreatorWorkflowRegressionError as exc:
        print(
            json.dumps(
                {"status": "failed", "message": str(exc)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    except Exception:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "message": "The creator-workflow regression could not be completed.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    print(
        json.dumps(
            {
                "status": "passed",
                "receipt_path": str(receipt_path),
                "receipt": receipt,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
