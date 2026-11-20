from __future__ import annotations

import pytest

pytest.importorskip("dagster")


def test_definicoes_carregam() -> None:
    from pipelines.definitions import defs

    assert defs.resolve_asset_graph().get_all_asset_keys()


def test_agendamento_semanal_desligado() -> None:
    from dagster import DefaultScheduleStatus
    from pipelines.definitions import defs

    agenda = defs.resolve_schedule_def("atualizacao_semanal")
    assert agenda.cron_schedule == "0 3 * * 0"
    assert agenda.default_status == DefaultScheduleStatus.STOPPED
    assert agenda.job_name == "atualizacao_completa"
