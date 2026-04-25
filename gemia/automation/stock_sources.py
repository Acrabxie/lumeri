from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import get_config_value, repo_root

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
STOPWORDS = {
    "with",
    "from",
    "real",
    "world",
    "clip",
    "test",
    "stock",
    "visual",
    "treatment",
    "natural",
    "motion",
    "mood",
}


class StockSourceError(RuntimeError):
    pass


class MissingStockSourceCredentials(StockSourceError):
    pass


@dataclass(frozen=True)
class StockAsset:
    outputs: list[str]
    backend: str
    source: str
    attribution: str = ""


def stock_query(prompt: str) -> str:
    words = re.findall(r"[a-zA-Z][a-zA-Z-]{2,}", prompt.lower())
    picked: list[str] = []
    for word in words:
        word = word.strip("-")
        if word and word not in STOPWORDS and word not in picked:
            picked.append(word)
        if len(picked) >= 4:
            break
    return " ".join(picked) or "nature"


def _read_json_url(url: str, *, headers: dict[str, str] | None = None, timeout: int = 20) -> dict[str, Any]:
    request_headers = {"User-Agent": "Gemia-Automation/1.0", **(headers or {})}
    req = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _download_url(url: str, output_path: Path, *, headers: dict[str, str] | None = None) -> str:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    request_headers = {"User-Agent": "Gemia-Automation/1.0", **(headers or {})}
    req = urllib.request.Request(url, headers=request_headers)
    tmp_path = output_path.with_suffix(output_path.suffix + ".part")
    with urllib.request.urlopen(req, timeout=120) as response, tmp_path.open("wb") as fh:
        shutil.copyfileobj(response, fh)
    tmp_path.replace(output_path)
    return str(output_path.resolve())


class ExternalStockClient:
    def __init__(self, *, pexels_key: str | None = None, pixabay_key: str | None = None) -> None:
        self.pexels_key = (pexels_key or get_config_value("pexels_api_key", "PEXELS_API_KEY")).strip()
        self.pixabay_key = (pixabay_key or get_config_value("pixabay_api_key", "PIXABAY_API_KEY")).strip()

    def has_credentials(self) -> bool:
        return bool(self.pexels_key or self.pixabay_key)

    def download_video(self, *, prompt: str, output_path: Path) -> StockAsset:
        if not self.has_credentials():
            raise MissingStockSourceCredentials("PEXELS_API_KEY or PIXABAY_API_KEY is required for web stock video.")
        errors: list[str] = []
        if self.pexels_key:
            try:
                return self._download_pexels_video(prompt=prompt, output_path=output_path)
            except Exception as exc:
                errors.append(f"pexels: {exc}")
        if self.pixabay_key:
            try:
                return self._download_pixabay_video(prompt=prompt, output_path=output_path)
            except Exception as exc:
                errors.append(f"pixabay: {exc}")
        raise StockSourceError("; ".join(errors) or "no external stock video source returned a result")

    def download_images(self, *, prompt: str, output_dir: Path, task_id: str, count: int) -> StockAsset:
        if not self.has_credentials():
            raise MissingStockSourceCredentials("PEXELS_API_KEY or PIXABAY_API_KEY is required for web stock images.")
        errors: list[str] = []
        if self.pexels_key:
            try:
                return self._download_pexels_images(prompt=prompt, output_dir=output_dir, task_id=task_id, count=count)
            except Exception as exc:
                errors.append(f"pexels: {exc}")
        if self.pixabay_key:
            try:
                return self._download_pixabay_images(prompt=prompt, output_dir=output_dir, task_id=task_id, count=count)
            except Exception as exc:
                errors.append(f"pixabay: {exc}")
        raise StockSourceError("; ".join(errors) or "no external stock image source returned a result")

    def _download_pexels_video(self, *, prompt: str, output_path: Path) -> StockAsset:
        query = urllib.parse.urlencode({"query": stock_query(prompt), "orientation": "landscape", "size": "small", "per_page": "6"})
        data = _read_json_url(
            f"https://api.pexels.com/v1/videos/search?{query}",
            headers={"Authorization": self.pexels_key},
        )
        candidates: list[dict[str, Any]] = []
        for video in data.get("videos", []):
            for item in video.get("video_files", []):
                link = str(item.get("link", ""))
                if link and str(item.get("file_type", "")).startswith("video/"):
                    candidates.append({**item, "page": video.get("url", ""), "user": video.get("user", {})})
        if not candidates:
            raise StockSourceError("Pexels returned no downloadable video files.")
        picked = min(candidates, key=lambda item: abs(int(item.get("width") or 1280) - 1280))
        output = _download_url(str(picked["link"]), output_path)
        user = picked.get("user") or {}
        attribution = f"Pexels {user.get('name', '')} {picked.get('page', '')}".strip()
        return StockAsset([output], "pexels", str(picked.get("page") or picked["link"]), attribution)

    def _download_pixabay_video(self, *, prompt: str, output_path: Path) -> StockAsset:
        query = urllib.parse.urlencode({"key": self.pixabay_key, "q": stock_query(prompt), "video_type": "film", "safesearch": "true", "per_page": "6"})
        data = _read_json_url(f"https://pixabay.com/api/videos/?{query}")
        choices: list[dict[str, Any]] = []
        for hit in data.get("hits", []):
            videos = hit.get("videos", {})
            for size in ("medium", "small", "tiny"):
                item = videos.get(size) or {}
                if item.get("url"):
                    choices.append({**item, "page": hit.get("pageURL", ""), "user": hit.get("user", ""), "size_name": size})
                    break
        if not choices:
            raise StockSourceError("Pixabay returned no downloadable video files.")
        picked = min(choices, key=lambda item: abs(int(item.get("width") or 1280) - 1280))
        output = _download_url(str(picked["url"]), output_path)
        attribution = f"Pixabay {picked.get('user', '')} {picked.get('page', '')}".strip()
        return StockAsset([output], "pixabay", str(picked.get("page") or picked["url"]), attribution)

    def _download_pexels_images(self, *, prompt: str, output_dir: Path, task_id: str, count: int) -> StockAsset:
        query = urllib.parse.urlencode({"query": stock_query(prompt), "orientation": "landscape", "size": "small", "per_page": str(max(count, 1))})
        data = _read_json_url(
            f"https://api.pexels.com/v1/search?{query}",
            headers={"Authorization": self.pexels_key},
        )
        outputs: list[str] = []
        attributions: list[str] = []
        for index, photo in enumerate(data.get("photos", [])[: max(count, 1)], start=1):
            src = photo.get("src", {})
            url = src.get("large") or src.get("landscape") or src.get("medium")
            if not url:
                continue
            outputs.append(_download_url(str(url), output_dir / f"{task_id}_{index:02d}.jpg"))
            attributions.append(f"Pexels {photo.get('photographer', '')} {photo.get('url', '')}".strip())
        if not outputs:
            raise StockSourceError("Pexels returned no downloadable images.")
        return StockAsset(outputs, "pexels", "pexels:image-search", "; ".join(attributions))

    def _download_pixabay_images(self, *, prompt: str, output_dir: Path, task_id: str, count: int) -> StockAsset:
        query = urllib.parse.urlencode({"key": self.pixabay_key, "q": stock_query(prompt), "image_type": "photo", "orientation": "horizontal", "safesearch": "true", "per_page": str(max(count, 3))})
        data = _read_json_url(f"https://pixabay.com/api/?{query}")
        outputs: list[str] = []
        attributions: list[str] = []
        for index, hit in enumerate(data.get("hits", [])[: max(count, 1)], start=1):
            url = hit.get("largeImageURL") or hit.get("webformatURL")
            if not url:
                continue
            outputs.append(_download_url(str(url), output_dir / f"{task_id}_{index:02d}.jpg"))
            attributions.append(f"Pixabay {hit.get('user', '')} {hit.get('pageURL', '')}".strip())
        if not outputs:
            raise StockSourceError("Pixabay returned no downloadable images.")
        return StockAsset(outputs, "pixabay", "pixabay:image-search", "; ".join(attributions))


class LocalStockClient:
    def __init__(self, *, roots: list[Path] | None = None, max_depth: int = 5) -> None:
        self.roots = roots or self._default_roots()
        self.max_depth = max_depth

    @staticmethod
    def _default_roots() -> list[Path]:
        env_roots = [Path(item).expanduser() for item in os.environ.get("GEMIA_LOCAL_STOCK_ROOTS", "").split(os.pathsep) if item.strip()]
        return env_roots + [
            repo_root() / "inputs",
            Path.home() / ".gemia" / "workspace" / "inputs",
            Path.home() / "Desktop",
        ]

    def copy_video(self, *, prompt: str, output_path: Path, used_sources: set[str] | None = None) -> StockAsset:
        source = self._choose_video(prompt, used_sources or set())
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output_path)
        return StockAsset([str(output_path.resolve())], "local_real_video", str(source.resolve()))

    def extract_images(self, *, prompt: str, output_dir: Path, task_id: str, count: int, used_sources: set[str] | None = None) -> StockAsset:
        source = self._choose_video(prompt, used_sources or set())
        output_dir.mkdir(parents=True, exist_ok=True)
        outputs: list[str] = []
        for index in range(max(int(count), 1)):
            output = output_dir / f"{task_id}_{index + 1:02d}.png"
            timestamp = f"{index * 0.5:.2f}"
            command = ["ffmpeg", "-y", "-ss", timestamp, "-i", str(source), "-frames:v", "1", str(output)]
            try:
                subprocess.run(command, check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError:
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", "0", "-i", str(source), "-frames:v", "1", str(output)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            outputs.append(str(output.resolve()))
        return StockAsset(outputs, "local_video_frame", str(source.resolve()))

    def _choose_video(self, prompt: str, used_sources: set[str]) -> Path:
        candidates = [path for path in self._video_candidates() if str(path.resolve()) not in used_sources]
        if not candidates:
            candidates = self._video_candidates()
        if not candidates:
            raise StockSourceError("No local real video assets found for stock fallback.")
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        return candidates[int(digest, 16) % len(candidates)]

    def _video_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        skipped_dirs = {".git", "node_modules", "__pycache__", ".venv", "Library"}
        for root in self.roots:
            root = root.expanduser()
            if not root.exists():
                continue
            base_depth = len(root.parts)
            for current, dirs, files in os.walk(root):
                current_path = Path(current)
                depth = len(current_path.parts) - base_depth
                if depth >= self.max_depth:
                    dirs[:] = []
                dirs[:] = [item for item in dirs if item not in skipped_dirs and not item.startswith(".")]
                for filename in files:
                    path = current_path / filename
                    if path.suffix.lower() in VIDEO_EXTENSIONS:
                        candidates.append(path)
        return sorted(candidates)
