#!/usr/bin/env python3
"""Validate Apple bundle IDs and Android application IDs against one contract."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SCHEMA = "lumeri.product-identities.v1"
IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+$")
APPLE_BUNDLE_PATTERN = re.compile(
    r"PRODUCT_BUNDLE_IDENTIFIER\s*=\s*\"?([^\";\s]+)\"?\s*;"
)
ANDROID_APPLICATION_PATTERN = re.compile(r'\bapplicationId\s*=\s*"([^"]+)"')


class ContractError(ValueError):
    """The identity contract or a consumer binding is invalid."""


def load_contract(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != SCHEMA:
        raise ContractError(f"expected schema {SCHEMA!r}")

    prefix = payload.get("bundle_id_prefix")
    products = payload.get("products")
    if not isinstance(prefix, str) or not IDENTIFIER_PATTERN.fullmatch(prefix):
        raise ContractError("bundle_id_prefix must be a lowercase reverse-DNS id")
    if not isinstance(products, dict) or not products:
        raise ContractError("products must be a non-empty object")

    for product_id, product in products.items():
        if not isinstance(product_id, str) or not re.fullmatch(r"[a-z][a-z0-9]*", product_id):
            raise ContractError(f"invalid product id: {product_id!r}")
        if not isinstance(product, dict):
            raise ContractError(f"product {product_id!r} must be an object")
        expected = f"{prefix}.{product_id}"
        if product.get("model") != product_id:
            raise ContractError(f"product {product_id!r} model must equal its key")
        for field in ("apple_bundle_id", "android_application_id"):
            actual = product.get(field)
            if actual != expected:
                raise ContractError(
                    f"product {product_id!r} {field} must be {expected!r}, got {actual!r}"
                )
    return payload


def parse_binding(value: str) -> tuple[str, Path]:
    product_id, separator, raw_path = value.partition("=")
    if not separator or not product_id or not raw_path:
        raise argparse.ArgumentTypeError("expected PRODUCT=PATH")
    return product_id, Path(raw_path).expanduser().resolve()


def expected_identity(contract: dict[str, Any], product_id: str, field: str) -> str:
    products = contract["products"]
    if product_id not in products:
        raise ContractError(f"unknown product {product_id!r}")
    return str(products[product_id][field])


def check_apple_project(
    contract: dict[str, Any], product_id: str, project_file: Path
) -> list[str]:
    if not project_file.is_file():
        return [f"Apple project file does not exist: {project_file}"]
    identifiers = set(APPLE_BUNDLE_PATTERN.findall(project_file.read_text(encoding="utf-8")))
    expected = expected_identity(contract, product_id, "apple_bundle_id")
    if expected not in identifiers:
        return [
            f"{product_id} Apple bundle id mismatch: expected {expected}, "
            f"found {sorted(identifiers)}"
        ]

    prefix = contract["bundle_id_prefix"]
    unexpected = sorted(
        identifier
        for identifier in identifiers
        if identifier.startswith(f"{prefix}.")
        and identifier != expected
        and not identifier.startswith(f"{expected}.")
    )
    if unexpected:
        return [f"{product_id} Apple project contains foreign family ids: {unexpected}"]
    return []


def check_android_gradle(
    contract: dict[str, Any], product_id: str, gradle_file: Path
) -> list[str]:
    if not gradle_file.is_file():
        return [f"Android Gradle file does not exist: {gradle_file}"]
    identifiers = set(
        ANDROID_APPLICATION_PATTERN.findall(gradle_file.read_text(encoding="utf-8"))
    )
    expected = expected_identity(contract, product_id, "android_application_id")
    if identifiers != {expected}:
        return [
            f"{product_id} Android application id mismatch: expected {expected}, "
            f"found {sorted(identifiers)}"
        ]
    return []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--contract",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "config" / "product-identities.json",
    )
    parser.add_argument(
        "--apple",
        action="append",
        default=[],
        type=parse_binding,
        metavar="PRODUCT=PBXPROJ",
    )
    parser.add_argument(
        "--android",
        action="append",
        default=[],
        type=parse_binding,
        metavar="PRODUCT=BUILD_GRADLE_KTS",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        contract = load_contract(args.contract.resolve())
        errors: list[str] = []
        for product_id, project_file in args.apple:
            errors.extend(check_apple_project(contract, product_id, project_file))
        for product_id, gradle_file in args.android:
            errors.extend(check_android_gradle(contract, product_id, gradle_file))
    except (ContractError, json.JSONDecodeError, OSError) as exc:
        print(f"identity contract error: {exc}", file=sys.stderr)
        return 2

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    checked = len(args.apple) + len(args.android)
    print(f"product identity contract OK ({checked} consumer(s) checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
