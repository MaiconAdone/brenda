from __future__ import annotations

import pytest

pytest.importorskip("dagster")


def test_definicoes_carregam() -> None:
    from pipelines.definitions import defs

    assert defs.resolve_asset_graph().get_all_asset_keys()
