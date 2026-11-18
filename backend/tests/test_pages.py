"""The two public HTML boards: planning, card mapping and failure isolation."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.pages import (
    COMPUTRABAJO_TERMS,
    fetch_computrabajo,
    fetch_weremoto,
    parse_computrabajo,
    parse_weremoto,
)
from app.worker import (
    COMPUTRABAJO_HOUR,
    WEREMOTO_HOUR,
    SourcePlan,
    adapt_rows,
    due_plans,
)

AR = timezone(timedelta(hours=-3))
TODAY = date(2026, 11, 18)


def at(hour: int) -> datetime:
    return datetime(2026, 11, 18, hour, tzinfo=AR)


WEREMOTO_CARD = """
<article class="card">
  <h3><a href="/job-posts/id-web-designer-pavago-1">Web Designer</a></h3>
  <p>pavago</p>
  <div><span>Remoto</span><span>18 nov</span>
    <a href="/job-posts/id-web-designer-pavago-1">Ver trabajo</a></div>
  <div><div><span>Full Time</span><span>Programación</span></div></div>
  <details><summary>Ver descripción Ocultar descripción</summary>
    <p>Rol: Web Designer en Pavago. Ubicación: Remoto - Argentina.</p>
  </details>
</article>
"""

COMPUTRABAJO_CARD = """
<article class="box_offer sel " data-id='1EEAA028AE88572861373E686DCF3405'>
  <h2 class="fs18 fwB prB">
    <a class="js-o-link fc_base"
       href="/ofertas-de-trabajo/oferta-de-trabajo-de-desarrolladora-frontend-ssrsr-en-retiro-1EEAA028AE88572861373E686DCF3405#lc=ListOffers-Score4-0">
      Desarrollador/a Frontend SSR/SR
    </a>
  </h2>
  <p class="dFlex vm_fx fs16 fc_base mt5"><span class="icon i_verificada mr5"></span>
    <a class="fc_base t_ellipsis"
       href="https://ar.computrabajo.com/empresas/ofertas-de-trabajo-de-america-virtual"
       offer-grid-article-company-url>AMERICA VIRTUAL S.A.</a></p>
  <p class="fs16 fc_base mt5"><span class="mr10">Retiro, Capital Federal</span></p>
  <div class="fs13 mt15"><span class="dIB mr10"><span class="icon i_home_office"></span>Presencial y remoto</span></div>
  <p class="fs13 fc_aux mt15">Hace  6  d&#xED;as</p>
</article>
"""


def test_page_sources_run_once_only_on_their_own_hour() -> None:
    morning = due_plans(at(WEREMOTO_HOUR))
    assert [p.name for p in morning if p.kind == "weremoto"] == ["weremoto"]
    assert not any(p.kind == "computrabajo" for p in morning)

    later = due_plans(at(COMPUTRABAJO_HOUR))
    assert [p.name for p in later if p.kind == "computrabajo"] == [
        f"computrabajo:{term}" for term in COMPUTRABAJO_TERMS
    ]
    assert [p.params["term"] for p in later if p.kind == "computrabajo"] == list(
        COMPUTRABAJO_TERMS
    )
    assert not any(p.kind == "weremoto" for p in later)
    assert not any(p.kind in {"weremoto", "computrabajo"} for p in due_plans(at(15)))


def test_a_weremoto_card_becomes_a_remote_job_with_its_own_description() -> None:
    rows = parse_weremoto(WEREMOTO_CARD, today=TODAY)
    assert len(rows) == 1

    job = adapt_rows(SourcePlan(name="weremoto", kind="weremoto"), rows)[0]
    assert job.title == "Web Designer"
    assert job.company == "pavago"
    assert job.is_remote is True
    assert job.job_type == "Full Time"
    assert job.posted_at == TODAY
    assert job.description.startswith("Rol: Web Designer")
    assert job.sources[0].source == "weremoto"
    assert job.sources[0].url == "https://www.weremoto.com/job-posts/id-web-designer-pavago-1"


def test_a_computrabajo_card_keeps_an_absolute_url_and_its_posted_age() -> None:
    rows = parse_computrabajo(COMPUTRABAJO_CARD, today=TODAY)
    assert len(rows) == 1

    job = adapt_rows(SourcePlan(name="computrabajo:react", kind="computrabajo"), rows)[0]
    assert job.title == "Desarrollador/a Frontend SSR/SR"
    assert job.company == "AMERICA VIRTUAL S.A."
    assert job.location == "Retiro, Capital Federal"
    assert job.is_remote is True
    assert job.posted_at == TODAY - timedelta(days=6)
    assert job.sources[0].source == "computrabajo"
    assert job.sources[0].url == (
        "https://ar.computrabajo.com/ofertas-de-trabajo/"
        "oferta-de-trabajo-de-desarrolladora-frontend-ssrsr-en-retiro-"
        "1EEAA028AE88572861373E686DCF3405"
    )


def test_cards_without_a_real_link_are_dropped_and_fetch_errors_raise() -> None:
    broken = (
        '<article class="box_offer" data-id="x"><h2>'
        '<a href="javascript:void(0)">Sin enlace</a></h2></article>'
    )
    assert parse_computrabajo(broken, today=TODAY) == []

    with pytest.raises(RuntimeError, match="weremoto returned http 403"):
        fetch_weremoto(transport=lambda *args, **kwargs: (403, b""))
    with pytest.raises(RuntimeError, match="computrabajo returned http 520"):
        fetch_computrabajo("react", transport=lambda *args, **kwargs: (520, b""))
