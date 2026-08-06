"""Focused checks for the static digest renderer's identity joins."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def renderer():
    path = Path(__file__).parents[1] / "scripts" / "build_github_pages.py"
    spec = importlib.util.spec_from_file_location("build_github_pages", path)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load static renderer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("url", "paper_id"),
    [
        ("https://arxiv.org/abs/2606.31227v2", "arxiv:2606.31227"),
        ("https://arxiv.org/pdf/hep-th/9901001.pdf", "arxiv:hep-th/9901001"),
        ("https://doi.org/10.1145/123.456/", "doi:10.1145/123.456"),
        ("https://openreview.net/forum?id=abc%2Fxyz", "openreview:abc/xyz"),
    ],
)
def test_paper_id_from_url_normalizes_supported_sources(renderer, url, paper_id):
    assert renderer._paper_id_from_url(url) == paper_id


def test_manifest_bucket_map_accepts_materializer_tracks(renderer):
    manifest = {
        "selection_decisions": {
            "doi:main": {"paper_id": "doi:main", "track": "core"},
            "openreview:other": {"paper_id": "openreview:other", "track": "broad"},
        }
    }

    assert renderer._manifest_bucket_map(manifest) == {
        "doi:main": "main_track",
        "openreview:other": "others",
    }


def test_assign_manifest_buckets_joins_frozen_ids_by_readme_order(renderer):
    papers = [{"paper_id": "https://official.example/main"}, {"paper_id": "arxiv:other"}]
    manifest = {
        "selection_decisions": {
            "official:record": {"bucket": "main_track"},
            "arxiv:other": {"bucket": "others"},
        }
    }

    renderer.assign_manifest_buckets(papers, manifest, ["official:record", "arxiv:other"])

    assert [paper["bucket"] for paper in papers] == ["main_track", "others"]
