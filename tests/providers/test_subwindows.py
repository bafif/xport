from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tweet_extractor.providers.subwindows import subwindows


def test_subwindows_un_solo_tramo_si_rango_menor_que_paso():
    since = datetime(2023, 1, 1, tzinfo=UTC)
    until = datetime(2023, 1, 5, tzinfo=UTC)
    assert list(subwindows(since, until, step_days=7)) == [(since, until)]


def test_subwindows_trocea_sin_solape_ni_hueco():
    since = datetime(2023, 1, 1, tzinfo=UTC)
    until = datetime(2023, 1, 15, tzinfo=UTC)
    assert list(subwindows(since, until, step_days=7)) == [
        (datetime(2023, 1, 1, tzinfo=UTC), datetime(2023, 1, 8, tzinfo=UTC)),
        (datetime(2023, 1, 8, tzinfo=UTC), datetime(2023, 1, 15, tzinfo=UTC)),
    ]


def test_subwindows_ultimo_tramo_recortado_a_until():
    since = datetime(2023, 1, 1, tzinfo=UTC)
    until = datetime(2023, 1, 10, tzinfo=UTC)
    assert list(subwindows(since, until, step_days=7)) == [
        (datetime(2023, 1, 1, tzinfo=UTC), datetime(2023, 1, 8, tzinfo=UTC)),
        (datetime(2023, 1, 8, tzinfo=UTC), datetime(2023, 1, 10, tzinfo=UTC)),
    ]


def test_subwindows_paso_de_un_dia():
    since = datetime(2023, 1, 1, tzinfo=UTC)
    until = datetime(2023, 1, 3, tzinfo=UTC)
    assert list(subwindows(since, until, step_days=1)) == [
        (datetime(2023, 1, 1, tzinfo=UTC), datetime(2023, 1, 2, tzinfo=UTC)),
        (datetime(2023, 1, 2, tzinfo=UTC), datetime(2023, 1, 3, tzinfo=UTC)),
    ]


def test_subwindows_rechaza_naive():
    with pytest.raises(ValueError):
        list(subwindows(datetime(2023, 1, 1), datetime(2023, 1, 8), step_days=7))


def test_subwindows_rechaza_since_posterior_o_igual():
    with pytest.raises(ValueError):
        list(subwindows(datetime(2023, 1, 8, tzinfo=UTC), datetime(2023, 1, 1, tzinfo=UTC), 7))


def test_subwindows_rechaza_step_menor_a_uno():
    with pytest.raises(ValueError):
        list(subwindows(datetime(2023, 1, 1, tzinfo=UTC), datetime(2023, 1, 8, tzinfo=UTC), 0))
